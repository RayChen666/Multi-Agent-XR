import importlib
import re
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Dict, List, Optional

import yaml
from pydantic import ValidationError

from skills._common import SkillContext
from tools.spacing import prompt_values

"""
Skill registry: each sub-folder of backEnd/skills/ holding a SKILL.md is one skill.

Progressive disclosure:
  Level 1  name + description + arg schema  -> LanguageAgent prompt (every request)
  Level 2  SKILL.md body                    -> SceneAgent LLM prompt (retries only)
  Level 3  skill.py run()/postcondition()   -> called by code, never shown to the LLM

skill.py must define:
  Args                                   pydantic model of the arguments
  run(args, ctx) -> Optional[Dict]       placement in SceneAgent output shape, or None
  postcondition(args, placement, ctx)    -> list of {"message": str, "fixable": bool}
and may define:
  infer(parsed_command) -> Optional[Dict]   regex fallback for the args
  INFER_PRIORITY = int                      order among infer() fallbacks (lower first)
  ACCEPT_ON_EXHAUSTED = bool                execute anyway once retries run out (default False)
  TRIGGER = regex                           words the user's prompt must contain for the LLM's
                                            choice of this skill to stand (e.g. "closer|farther")

SKILL.md frontmatter `selectable: false` hides a skill from the LanguageAgent
(its args come from another agent, e.g. graph_layout from the Memory route).
"""

SKILLS_DIR = Path(__file__).resolve().parent


class Skill:
    def __init__(self, folder: Path):
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        _, frontmatter, body = text.split("---", 2)
        meta = yaml.safe_load(frontmatter)
        self.name: str = meta["name"]
        self.description: str = " ".join(meta["description"].split())
        # $NAME placeholders are filled from tools/spacing.py so the rules the LLM reads
        # always carry the numbers the tools use.
        self.body: str = Template(body.strip()).safe_substitute(prompt_values())
        self.selectable: bool = meta.get("selectable", True)
        self.module = importlib.import_module(f"skills.{folder.name}.skill")
        self.Args = self.module.Args
        self.accept_on_exhausted: bool = getattr(self.module, "ACCEPT_ON_EXHAUSTED", False)
        trigger = getattr(self.module, "TRIGGER", None)
        self.trigger = re.compile(trigger, re.IGNORECASE) if trigger else None

    def parse_args(self, raw: Dict):
        return self.Args(**raw)

    def run(self, raw_args: Dict, ctx: SkillContext) -> Optional[Dict]:
        return self.module.run(self.parse_args(raw_args), ctx)

    def postcondition(self, raw_args: Dict, placement: Dict, ctx: SkillContext) -> List[Dict]:
        return self.module.postcondition(self.parse_args(raw_args), placement, ctx)

    def index_entry(self) -> str:
        """Level-1 text: one block per skill for the LanguageAgent prompt."""
        schema = self.Args.model_json_schema()
        required = set(schema.get("required", []))
        lines = [f"- {self.name}: {self.description}", "    args:"]
        for arg, spec in schema.get("properties", {}).items():
            kind = "|".join(f'"{v}"' for v in spec["enum"]) if "enum" in spec else spec.get("type", "any")
            flag = "required" if arg in required else f"optional, default {spec.get('default')!r}"
            lines.append(f"      {arg} ({kind}, {flag}): {spec.get('description', '')}")
        return "\n".join(lines)


class SkillRegistry:
    def __init__(self, root: Path = SKILLS_DIR):
        self.skills: Dict[str, Skill] = {}
        for folder in sorted(root.iterdir()):
            if (folder / "SKILL.md").exists():
                skill = Skill(folder)
                self.skills[skill.name] = skill

    def get(self, name: Optional[str]) -> Optional[Skill]:
        return self.skills.get(name) if name else None

    def index_prompt(self) -> str:
        selectable = [s for s in self.skills.values() if s.selectable]
        if not selectable:
            return ""
        entries = "\n".join(s.index_entry() for s in selectable)
        return f"""

        SPATIAL SKILLS (deterministic placement tools):
        Add a top-level "skill" field to your JSON output.
        - Set it to {{"name": <skill name>, "args": {{...}}}} ONLY when the command clearly
          matches ONE skill below and every required arg can be filled from the command.
        - Otherwise set "skill": null — general spatial reasoning will handle it.
        - Write object phrases as the user said them, singular, without articles
          ("coffee table", not "the coffee tables"). For ADD commands, "target" is the new object.

        Available skills:
{entries}
        """

    def infer_call(self, parsed_command: Dict) -> Optional[Dict]:
        """
        Regex fallback when the LLM chose no skill: ask each skill's optional
        infer(parsed_command) in INFER_PRIORITY order; first valid hit wins.
        """
        selectable = [s for s in self.skills.values() if s.selectable]
        ordered = sorted(selectable, key=lambda s: getattr(s.module, "INFER_PRIORITY", 99))
        for skill in ordered:
            infer = getattr(skill.module, "infer", None)
            raw = infer(parsed_command) if infer else None
            if raw:
                call = self.validate_call({"name": skill.name, "args": raw}, source="regex",
                                          prompt=parsed_command.get("original_prompt"))
                if call:
                    return call
        return None

    def validate_call(self, call, source: str = "llm", prompt: Optional[str] = None) -> Optional[Dict]:
        """
        Normalize a skill call; anything unusable becomes None. `source` records who
        chose it ("llm" | "regex" | an agent such as "memory") for logs and analysis.
        LLM/regex calls may only name selectable skills. When `prompt` (the user's words)
        is given, a skill with a TRIGGER is rejected unless the prompt matches it.
        """
        if not isinstance(call, dict):
            return None
        skill = self.get(call.get("name"))
        if skill and source in ("llm", "regex") and not skill.selectable:
            print(f"   [SKILL] '{skill.name}' is not LLM-selectable, ignoring")
            return None
        if not skill:
            if call.get("name"):
                print(f"   [SKILL] unknown skill '{call.get('name')}', ignoring")
            return None
        try:
            args = skill.parse_args(call.get("args") or {})
        except ValidationError as e:
            print(f"   [SKILL] invalid args for '{skill.name}': {e.errors()}")
            return None
        if skill.trigger and prompt is not None and not skill.trigger.search(prompt):
            print(f"   [SKILL] '{skill.name}' rejected: \"{prompt}\" has none of its trigger words")
            return None
        return {"name": skill.name, "args": args.model_dump(by_alias=True), "source": source}

    @staticmethod
    def describe(call: Optional[Dict]) -> str:
        """One-line log form: 'graph_layout [memory] edges=<9 items>, anchor_object='sink'."""
        if not call:
            return "none"
        args = ", ".join(f"{k}=<{len(v)} items>" if isinstance(v, list) else f"{k}={v!r}"
                         for k, v in call["args"].items())
        return f"{call['name']} [{call.get('source', '?')}] {args}"


@lru_cache(maxsize=1)
def get_registry() -> SkillRegistry:
    return SkillRegistry()
