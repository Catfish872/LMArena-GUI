
Model
好的，明白了。您需要的是一份可以直接复制粘贴到GitHub，并且能正确显示标题、列表、代码块等格式的Markdown文档。
我将为您重新格式化，确保所有元素都符合Markdown语法规范。
code
Markdown
# LMArena Bridge - 自动化解决方案

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**一个稳定且完全自动化的API桥接服务，让您能将 LMArena (`lmarena.ai`) 强大的模型能力无缝对接到任何兼容OpenAI API的前端应用中，例如 SillyTavern、Open-WebUI 等。**

## 📖 这是什么？

LMArena Bridge 是一套完整的解决方案，由三个核心组件构成：

1.  **后端服务 (`api_server.py`)**: 扮演大脑的角色，接收标准OpenAI格式的API请求，并指挥其他组件工作。
2.  **浏览器插件 (`LMArenaApiBridge.js`)**: 作为在浏览器中的内应，负责填充文本、激活按钮，并捕获模型的响应数据。
3.  **GUI点击器 (`clicker.py`)**: 像一只精准的机械手臂，在操作系统层面完成最关键的“鼠标点击”操作，以绕过所有浏览器安全限制。

三者协同工作，实现了一个看似不可能的任务：**在一个对真人用户都有限制的网页上，实现100%的全自动、无人值守的API服务。**

## 🎯 解决了什么痛点？

许多自动化方案都失败于现代网页的“人机验证”和“浏览器安全沙箱”，具体痛点如下：

*   **脚本点击无效**: LMArena 使用了如React等现代前端框架，通过简单的JavaScript `button.click()` 无法触发真正的、可信的发送事件。
*   **浏览器限制**: 严格的安全策略（如CSP）和人机验证（如Cloudflare）会阻止自动化脚本的许多行为。
*   **窗口激活要求**: 许多浏览器API在窗口处于非激活状态（最小化或被遮挡）时会失效。

**LMArena Bridge 通过一个巧妙的架构彻底解决了这些问题：**

它将浏览器置于一个拥有完整图形界面（GUI）的“完美环境”（虚拟机或物理服务器）中，并始终保持其处于激活状态。然后，通过后端指挥浏览器插件完成“准备工作”，最后派出一个操作系统级的“机械手臂”来完成那一下无法被伪造的**物理点击**。

---

## 🚀 快速开始：安装与使用指南

请严格遵循以下步骤，即使您是新手也能成功部署。

### 📌 **第一步：环境准备 (至关重要)**

本项目**强制要求**一个带图形界面的Linux环境。我们推荐使用虚拟机，因为它能提供一个干净、隔离的运行环境。

1.  **安装虚拟机**: 下载并安装 [VirtualBox](https://www.virtualbox.org/) 或 [VMware Workstation Player](https://www.vmware.com/products/workstation-player.html)。
2.  **安装Linux系统**: 下载 [Kali Linux](https://www.kali.org/get-kali/#kali-virtual-machines) 的虚拟机镜像并导入。Kali Linux 自带了大量实用工具，能省去很多配置麻烦。
3.  **安装核心软件**: 在您的Kali虚拟机中，打开终端，安装本项目所需的核心软件：
    ````bash
    # 更新软件包列表
    sudo apt-get update

    # 安装 Google Chrome 浏览器
    wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt install ./google-chrome-stable_current_amd64.deb -y

    # 安装 Git 和 Python 依赖
    sudo apt-get install -y git python3-pip python3-tk python3-dev scrot
    ````

### 🛠 **第二步：项目配置**

1.  **克隆项目**: 在终端中，将本项目代码克隆到您的虚拟机中。
    ````bash
    git clone [您的项目GitHub仓库地址]
    cd [项目文件夹名称]
    ````

2.  **安装Python依赖**:
    ````bash
    pip install "uvicorn[standard]" fastapi requests packaging pyautogui
    ````

3.  **配置浏览器插件**:
    *   在虚拟机的Chrome浏览器中，安装 [Tampermonkey (油猴)](https://chrome.google.com/webstore/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo) 扩展。
    *   点击Tampermonkey图标 -> `Create a new script...`
    *   将项目中的 `LMArenaApiBridge.js` 的**全部内容**复制并粘贴到编辑器中，然后按 `Ctrl + S` 保存。

4.  **配置 `clicker.py` (最关键的手动步骤！)**
    
    `clicker.py` 需要知道“发送”按钮在您屏幕上的精确坐标。
    
    *   **首先，获取坐标**:
        *   在虚拟机中，**最大化** Chrome 浏览器窗口，并登录 LMArena。
        *   在终端中，运行一个我们预留的辅助脚本来帮你找到坐标：
            ````bash
            python -c "import pyautogui, time; print('请在5秒内将鼠标移动到发送按钮中心...'); time.sleep(5); x, y = pyautogui.position(); print(f'坐标已捕获: X={x}, Y={y}')"
            ````
        *   运行后，您有5秒时间将鼠标指针移动到LMArena页面“发送”按钮的正中心。5秒后，终端会打印出精确的 X, Y 坐标。
    
    *   **然后，修改文件**:
        *   用文本编辑器打开 `clicker.py` 文件。
        *   找到以下两行：
            ````python
            SEND_BUTTON_X = 1850 
            SEND_BUTTON_Y = 950
            ````
        *   将这里的 `1850` 和 `950` **替换为您刚刚获取到的 X 和 Y 坐标**。保存并关闭文件。

    > **⚠️ 重要提示**: 此坐标与您的屏幕分辨率和浏览器窗口大小严格绑定。为确保100%成功，请始终在**最大化**的浏览器窗口下获取坐标，并在后续运行时也**保持窗口最大化**。

### ▶️ **第三步：运行！**

现在，一切准备就绪，可以启动整套服务了。

1.  **启动后端服务**:
    *   在虚拟机中，打开一个终端，`cd`到项目文件夹。
    *   运行命令:
        ````bash
        python api_server.py
        ````
    *   看到 `Uvicorn running on http://0.0.0.0:5102` 的日志后，将其**保持运行**，不要关闭。

2.  **准备浏览器**:
    *   在虚拟机中，打开Chrome，访问 LMArena 并登录。
    *   **检查连接**: 切换回 `api_server.py` 的终端窗口，您**必须看到**一条绿色的日志 `✅ 油猴脚本已成功连接 WebSocket。`。这表示连接成功。
    *   **保持激活**: 将Chrome窗口**最大化**，并**保持在屏幕最前端**。不要最小化或遮挡它。

3.  **配置您的AI前端 (如 SillyTavern)**:
    *   首先，获取您虚拟机的IP地址。在虚拟机终端输入 `ifconfig`，找到类似 `192.168.x.x` 的地址。
    *   在您**主机**上的AI前端应用的API设置中：
        *   **API Base URL**: `http://[您虚拟机的IP地址]:5102/v1` (例如: `http://192.168.1.227:5102/v1`)
        *   **API Key**: 任意填写，或根据您`config.jsonc`中的设置填写。
    *   保存设置，开始聊天！

现在，您在主机上的每一次聊天，都会在虚拟机中触发一整套无人值守的自动化操作，并将结果无缝返回给您。

---

## 🔍 故障排查 (FAQ)

*   **Q: 我发送请求后，返回`503 Service Unavailable`错误，终端没有绿色`✅`日志。**
    *   **A:** 这是最常见的问题，原因是WebSocket连接失败。通常是由于浏览器的“混合内容”安全策略导致。请在虚拟机的Chrome中访问 `chrome://settings/content/insecureContent`，并在“允许”部分添加 `localhost:5102` 和 `127.0.0.1:5102`，然后刷新LMArena页面。

*   **Q: 自动化流程启动了，但鼠标没有点到按钮上，或者点了没反应。**
    *   **A:** 99%是坐标问题。请确保您获取坐标时和运行时，浏览器的窗口大小和位置**完全一致**。强烈建议始终使用最大化窗口。请重新执行**第二步第4点**来获取并更新坐标。

*   **Q: 我的AI前端一直显示“正在生成”，无法发送下一条消息。**
    *   **A:** 这是因为结束信号`[DONE]`没有被正确发送。请确保您使用的是最新版的油猴脚本，其中包含了 `response.clone()` 修复，可以解决流读取冲突问题。

---

希望这份文档能帮助您顺利部署和使用 LMArena Bridge！
