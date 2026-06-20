import trimesh
import json
import os
from pathlib import Path

"""
Running this script will extract collision dimensions for all assets in 
the gltf-glb-models directory and update their metadata.json files with 
a new "collision" field containing width, height, and depth. This is done 
by loading each model with trimesh, calculating its bounding box, and applying 
any default scaling from the metadata.
"""

def extract_collision_dims(assets_path: Path):
    assets_path = Path(assets_path)

    for obj_folder in assets_path.iterdir():
        if not obj_folder.is_dir():
            continue

        # find glb/gltf file
        metadata_path = obj_folder / "metadata.json"
        model_files = list(obj_folder.glob("*.gltf")) + list(obj_folder.glob("*.glb"))

        if not model_files or not metadata_path.exists():
            print(f"Skipping {obj_folder.name} — missing model or metadata")
            continue

        model_path = model_files[0]

        with open(metadata_path) as f:
            meta = json.load(f)
        
        scale = meta.get("default_scale", {"x": 1, "y": 1, "z": 1})


        try:
            mesh = trimesh.load(str(model_path), force='mesh')
            # [[min_x,min_y,min_z], [max_x,max_y,max_z]]
            bounds = mesh.bounds  
            raw = bounds[1] - bounds[0]

            # calculate the geometric center of the bounding box
            center = (bounds[0] + bounds[1]) / 2

            '''
            meta["collision"] = {
                "width":  round(float(raw[0] * scale["x"]), 3),
                "height": round(float(raw[1] * scale["y"]), 3),
                "depth":  round(float(raw[2] * scale["z"]), 3)
            }
            '''
            meta["collision"] = {
                "width":  round(float(raw[0] * scale["x"]), 3),
                "height": round(float(raw[1] * scale["y"]), 3),
                "depth":  round(float(raw[2] * scale["z"]), 3),
                "offset": {
                    "x": round(float(center[0] * scale["x"]), 3),
                    "y": round(float(center[1] * scale["y"]), 3),
                    "z": round(float(center[2] * scale["z"]), 3),
                }
            }
            
            with open(metadata_path, "w") as f:
                json.dump(meta, f, indent=2)
            
            print(f" {obj_folder.name}: {meta['collision']}")

        except Exception as e:
            print(f" {obj_folder.name}: {e}")


if __name__ == "__main__":
    assets_path = Path(__file__).resolve().parents[2] / "webXR" / "assets" / "gltf-glb-models"
    extract_collision_dims(assets_path)



