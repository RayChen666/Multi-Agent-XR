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
0. **Install python packages in the requirements.txt**:
    ```bash
    cd backEnd
    pip install -r requirements.txt
    ```

1. **Check your local IP address by running**:
    ```bash
    ifconfig | grep "inet " | grep -v 127.0.0.1
    ```
    
2. **Gather SSL certificate by running**: (you need to run this command everytime you change your IP address)
    ```bash
    cd backEnd
    openssl req -x509 -newkey rsa:4096 -nodes \
        -keyout key.pem -out cert.pem -days 365 \
        -subj "/C=US/ST=State/L=City/O=Dev/CN=your-local-ip-address"
    ```

3. **Open the terminal in the root project folder**:
    if you just want to test the backend, open terminal in *backEnd* folder, run 
    ```bash
    python main.py
    ```
    if you just want to test the frontend, open another terminal, navigate to the project root folder and run
    ```bash
    npm run dev
    ```
4. **Trust certificate on devices:**
    - Desktop: In Chrome browser visit https://localhost:8000 and accept warning
    - Headset: In Quest browser visit https://your-ip-address:8000 and accept warning

5. **Navigate to the scene**:
    To navigate to the scene, go to your browser (either on laptop or XR headset) and type: https://your-ip-address:8081/ for headset while https://localhost:8081/ for laptop browser, and accept warning.
    <p align="center">
    <img src="./docs/images (for README and experiment)/scene_image.png" />
    </p>

6. **Test update position function**:
    In the browser there is a chat box that you can type the command to manipulate the scene. Now it can take any natural language and do the spatial operation with multi-agent system setup.


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
