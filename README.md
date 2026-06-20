<p align="center">
    <img src="./docs/images (for README and experiment)/title_image.png" />
</p>

## Multi-Agent-XR

This tutorial teaches you how to navigate to the reseources and basic setup of the project.

## Agent System Workflow
<p align="center">
    <img src="./docs/images (for README and experiment)/agent_system_workflow.png" />
</p>

## Setup local environment

1. **Clone the repository**: 
    ```bash
    git clone https://github.com/RayChen666/Multi-Agent-XR.git
    ```
2. **Navigate to the project folder**:
    ```bash
    cd Multi-Agent-XR
    ```
3. **Install npm**:
    ```bash
    npm install
    ```

## Generate Google Gemini API key

1. **Go to website**:
    https://aistudio.google.com/api-keys to create your own Gemini API key following the giudeline.

2. **Navigate to root folder**: 
    - create *.env* file at root folder with one line code add to it:
    ```bash
    GEMINI_API_KEY=Your-Gemini-API-Key
    ```
    - add your own Gemini-API-Key generated from previous step.

## Open the project:
1. **Install python packages in the requirements.txt**:
    ```bash
    cd backEnd
    pip install -r requirements.txt
    ```

2. **Create key.pem and cert.pem in backEnd folder**:
    ```bash
    cd backEnd
    openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes
    ```
    Note: if this is your first time create permission files, just hit *enter* for all questiones popped up for local development.
    
3. **Check your local IP address by running**:
    ```bash
    ifconfig | grep "inet " | grep -v 127.0.0.1
    ```
    
4. **Gather SSL certificate by running**: (you need to run this command everytime you change your IP address)
    ```bash
    cd backEnd
    openssl req -x509 -newkey rsa:4096 -nodes \
        -keyout key.pem -out cert.pem -days 365 \
        -subj "/C=US/ST=State/L=City/O=Dev/CN=your-local-ip-address"
    ```

5. **Open the terminal in the root project folder**:
    if you just want to test the backend, open terminal in *backEnd* folder, run 
    ```bash
    cd backEnd
    python main.py
    ```
    if you just want to test the frontend, open another terminal, navigate to the project root folder and run
    ```bash
    cd Multi-Agent-XR
    npm run dev
    ```
6. **Trust certificate on devices:**
    - Desktop: In Chrome browser visit https://localhost:8000 and accept warning
    - Headset: In Quest browser visit https://your-ip-address:8000 and accept warning

7. **Navigate to the scene**:
    To navigate to the scene, go to your browser (either on laptop or XR headset) and type: https://your-ip-address:8081/ for headset while https://localhost:8081/ for laptop browser, and accept warning.
    <p align="center">
    <img src="./docs/images (for README and experiment)/scene_image.png" />
    </p>

8. **Test update position function**:
    In the browser there is a chat box that you can type the command to manipulate the scene. Now it can take any natural language and do the spatial operation with multi-agent system setup.

9. **Bonus: add your own layout and assets resources for personal VR world generation and interaction**:
    - add reference room layout: naviagte to /webXR/assets/reference_layouts folder, add your own room.jpg for template generation. For example, adding a living_room.jpg with up-view ( Sqaure image for precise layout mimic).
    - add objects: naviagte to /webXR/assets/gltf-glb-models folder, add your own .gltf/.glb objects. Be sure to put object file in a folder with its name, and create the metadata.json for regulating its default size.




## Install Immersive Web Emulator extension
Navigate to: https://chromewebstore.google.com/detail/immersive-web-emulator/cgffilbpcibhmcfbgggfhfolhkfbhmik?hl=en&pli=1 to install the extension for your Chrome browser.

## Relevant Resources (in docs folder)
1. **To understand the idea of the whole project:** go to 
    - */learningMaterial/Research Proposal.pdf* file
2. **To find related articles:** go to 
    - */learningMaterial/Articles* folder
3. **To find some useful learning resources and understand code archetecture:** go to   
    - */learningMaterial/myResearchNote.txt* file
4. **To try with ML prototype or do your own experiment:** go to
    - */jupyterNotebook (for prototype)* folder
5. **To keep some historical version of codes:** store your code (in .txt format) in
    - */historicalCodes* folder
