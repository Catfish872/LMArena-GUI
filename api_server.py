# api_server.py
# 新一代 LMArena Bridge 后端服务

import asyncio
import json
import logging
import os
import sys
import subprocess
import time
import uuid
import re
import threading
import random
import mimetypes
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
import requests
from packaging.version import parse as parse_version
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response

# --- 基础配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- 全局状态与配置 ---
CONFIG = {}  # 存储从 config.jsonc 加载的配置
# browser_ws 用于存储与单个油猴脚本的 WebSocket 连接。
# 注意：此架构假定只有一个浏览器标签页在工作。
# 如果需要支持多个并发标签页，需要将此扩展为字典管理多个连接。
browser_ws: WebSocket | None = None
# response_channels 用于存储每个 API 请求的响应队列。
# 键是 request_id，值是 asyncio.Queue。
response_channels: dict[str, dict] = {}
last_activity_time = None  # 记录最后一次活动的时间
idle_monitor_thread = None  # 空闲监控线程
main_event_loop = None  # 主事件循环

# --- 模型映射 ---
# MODEL_NAME_TO_ID_MAP 现在将存储更丰富的对象： { "model_name": {"id": "...", "type": "..."} }
MODEL_NAME_TO_ID_MAP = {}
MODEL_ENDPOINT_MAP = {}  # 新增：用于存储模型到 session/message ID 的映射
DEFAULT_MODEL_ID = None  # 默认模型id: None


def load_model_endpoint_map():
    """从 model_endpoint_map.json 加载模型到端点的映射。"""
    global MODEL_ENDPOINT_MAP
    try:
        with open('model_endpoint_map.json', 'r', encoding='utf-8') as f:
            content = f.read()
            # 允许空文件
            if not content.strip():
                MODEL_ENDPOINT_MAP = {}
            else:
                MODEL_ENDPOINT_MAP = json.loads(content)
        logger.info(f"成功从 'model_endpoint_map.json' 加载了 {len(MODEL_ENDPOINT_MAP)} 个模型端点映射。")
    except FileNotFoundError:
        logger.warning("'model_endpoint_map.json' 文件未找到。将使用空映射。")
        MODEL_ENDPOINT_MAP = {}
    except json.JSONDecodeError as e:
        logger.error(f"加载或解析 'model_endpoint_map.json' 失败: {e}。将使用空映射。")
        MODEL_ENDPOINT_MAP = {}


def load_config():
    """从 config.jsonc 加载配置，并处理 JSONC 注释。"""
    global CONFIG
    try:
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            content = f.read()
            # 移除 // 行注释和 /* */ 块注释
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            CONFIG = json.loads(json_content)
        logger.info("成功从 'config.jsonc' 加载配置。")
        # 打印关键配置状态
        logger.info(f"  - 酒馆模式 (Tavern Mode): {'✅ 启用' if CONFIG.get('tavern_mode_enabled') else '❌ 禁用'}")
        logger.info(f"  - 绕过模式 (Bypass Mode): {'✅ 启用' if CONFIG.get('bypass_enabled') else '❌ 禁用'}")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"加载或解析 'config.jsonc' 失败: {e}。将使用默认配置。")
        CONFIG = {}


def load_model_map():
    """从 models.json 加载模型映射，支持 'id:type' 格式。"""
    global MODEL_NAME_TO_ID_MAP
    try:
        with open('models.json', 'r', encoding='utf-8') as f:
            raw_map = json.load(f)

        processed_map = {}
        for name, value in raw_map.items():
            if isinstance(value, str) and ':' in value:
                parts = value.split(':', 1)
                model_id = parts[0] if parts[0].lower() != 'null' else None
                model_type = parts[1]
                processed_map[name] = {"id": model_id, "type": model_type}
            else:
                # 默认或旧格式处理
                processed_map[name] = {"id": value, "type": "text"}

        MODEL_NAME_TO_ID_MAP = processed_map
        logger.info(f"成功从 'models.json' 加载并解析了 {len(MODEL_NAME_TO_ID_MAP)} 个模型。")

    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"加载 'models.json' 失败: {e}。将使用空模型列表。")
        MODEL_NAME_TO_ID_MAP = {}


# --- 更新检查 ---
GITHUB_REPO = "Lianues/LMArenaBridge"


def download_and_extract_update(version):
    """下载并解压最新版本到临时文件夹。"""
    update_dir = "update_temp"
    if not os.path.exists(update_dir):
        os.makedirs(update_dir)

    try:
        zip_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
        logger.info(f"正在从 {zip_url} 下载新版本...")
        response = requests.get(zip_url, timeout=60)
        response.raise_for_status()

        # 需要导入 zipfile 和 io
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            z.extractall(update_dir)

        logger.info(f"新版本已成功下载并解压到 '{update_dir}' 文件夹。")
        return True
    except requests.RequestException as e:
        logger.error(f"下载更新失败: {e}")
    except zipfile.BadZipFile:
        logger.error("下载的文件不是一个有效的zip压缩包。")
    except Exception as e:
        logger.error(f"解压更新时发生未知错误: {e}")

    return False


def check_for_updates():
    """从 GitHub 检查新版本。"""
    if not CONFIG.get("enable_auto_update", True):
        logger.info("自动更新已禁用，跳过检查。")
        return

    current_version = CONFIG.get("version", "0.0.0")
    logger.info(f"当前版本: {current_version}。正在从 GitHub 检查更新...")

    try:
        config_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/config.jsonc"
        response = requests.get(config_url, timeout=10)
        response.raise_for_status()

        jsonc_content = response.text
        json_content = re.sub(r'//.*', '', jsonc_content)
        json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
        remote_config = json.loads(json_content)

        remote_version_str = remote_config.get("version")
        if not remote_version_str:
            logger.warning("远程配置文件中未找到版本号，跳过更新检查。")
            return

        if parse_version(remote_version_str) > parse_version(current_version):
            logger.info("=" * 60)
            logger.info(f"🎉 发现新版本! 🎉")
            logger.info(f"  - 当前版本: {current_version}")
            logger.info(f"  - 最新版本: {remote_version_str}")
            if download_and_extract_update(remote_version_str):
                logger.info("准备应用更新。服务器将在5秒后关闭并启动更新脚本。")
                time.sleep(5)
                update_script_path = os.path.join("modules", "update_script.py")
                # 使用 Popen 启动独立进程
                subprocess.Popen([sys.executable, update_script_path])
                # 优雅地退出当前服务器进程
                os._exit(0)
            else:
                logger.error(f"自动更新失败。请访问 https://github.com/{GITHUB_REPO}/releases/latest 手动下载。")
            logger.info("=" * 60)
        else:
            logger.info("您的程序已是最新版本。")

    except requests.RequestException as e:
        logger.error(f"检查更新失败: {e}")
    except json.JSONDecodeError:
        logger.error("解析远程配置文件失败。")
    except Exception as e:
        logger.error(f"检查更新时发生未知错误: {e}")


# --- 模型更新 ---
def extract_models_from_html(html_content):
    """
    从 HTML 内容中提取完整的模型JSON对象，使用括号匹配确保完整性。
    """
    models = []
    model_names = set()

    # 查找所有可能的模型JSON对象的起始位置
    for start_match in re.finditer(r'\{\\"id\\":\\"[a-f0-9-]+\\"', html_content):
        start_index = start_match.start()

        # 从起始位置开始，进行花括号匹配
        open_braces = 0
        end_index = -1

        # 优化：设置一个合理的搜索上限，避免无限循环
        search_limit = start_index + 10000  # 假设一个模型定义不会超过10000个字符

        for i in range(start_index, min(len(html_content), search_limit)):
            if html_content[i] == '{':
                open_braces += 1
            elif html_content[i] == '}':
                open_braces -= 1
                if open_braces == 0:
                    end_index = i + 1
                    break

        if end_index != -1:
            # 提取完整的、转义的JSON字符串
            json_string_escaped = html_content[start_index:end_index]

            # 反转义
            json_string = json_string_escaped.replace('\\"', '"').replace('\\\\', '\\')

            try:
                model_data = json.loads(json_string)
                model_name = model_data.get('publicName')

                # 使用publicName去重
                if model_name and model_name not in model_names:
                    models.append(model_data)
                    model_names.add(model_name)
            except json.JSONDecodeError as e:
                logger.warning(f"解析提取的JSON对象时出错: {e} - 内容: {json_string[:150]}...")
                continue

    if models:
        logger.info(f"成功提取并解析了 {len(models)} 个独立模型。")
        return models
    else:
        logger.error("错误：在HTML响应中找不到任何匹配的完整模型JSON对象。")
        return None


def save_available_models(new_models_list, models_path="available_models.json"):
    """
    将提取到的完整模型对象列表保存到指定的JSON文件中。
    """
    logger.info(f"检测到 {len(new_models_list)} 个模型，正在更新 '{models_path}'...")

    try:
        with open(models_path, 'w', encoding='utf-8') as f:
            # 直接将完整的模型对象列表写入文件
            json.dump(new_models_list, f, indent=4, ensure_ascii=False)
        logger.info(f"✅ '{models_path}' 已成功更新，包含 {len(new_models_list)} 个模型。")
    except IOError as e:
        logger.error(f"❌ 写入 '{models_path}' 文件时出错: {e}")


# --- 自动重启逻辑 ---
def restart_server():
    """优雅地通知客户端刷新，然后重启服务器。"""
    logger.warning("=" * 60)
    logger.warning("检测到服务器空闲超时，准备自动重启...")
    logger.warning("=" * 60)

    # 1. (异步) 通知浏览器刷新
    async def notify_browser_refresh():
        if browser_ws:
            try:
                # 优先发送 'reconnect' 指令，让前端知道这是一个计划内的重启
                await browser_ws.send_text(json.dumps({"command": "reconnect"}, ensure_ascii=False))
                logger.info("已向浏览器发送 'reconnect' 指令。")
            except Exception as e:
                logger.error(f"发送 'reconnect' 指令失败: {e}")

    # 在主事件循环中运行异步通知函数
    # 使用`asyncio.run_coroutine_threadsafe`确保线程安全
    if browser_ws and browser_ws.client_state.name == 'CONNECTED' and main_event_loop:
        asyncio.run_coroutine_threadsafe(notify_browser_refresh(), main_event_loop)

    # 2. 延迟几秒以确保消息发送
    time.sleep(3)

    # 3. 执行重启
    logger.info("正在重启服务器...")
    os.execv(sys.executable, ['python'] + sys.argv)


def idle_monitor():
    """在后台线程中运行，监控服务器是否空闲。"""
    global last_activity_time

    # 等待，直到 last_activity_time 被首次设置
    while last_activity_time is None:
        time.sleep(1)

    logger.info("空闲监控线程已启动。")

    while True:
        if CONFIG.get("enable_idle_restart", False):
            timeout = CONFIG.get("idle_restart_timeout_seconds", 300)

            # 如果超时设置为-1，则禁用重启检查
            if timeout == -1:
                time.sleep(10)  # 仍然需要休眠以避免繁忙循环
                continue

            idle_time = (datetime.now() - last_activity_time).total_seconds()

            if idle_time > timeout:
                logger.info(f"服务器空闲时间 ({idle_time:.0f}s) 已超过阈值 ({timeout}s)。")
                restart_server()
                break  # 退出循环，因为进程即将被替换

        # 每 10 秒检查一次
        time.sleep(10)


# --- FastAPI 生命周期事件 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """在服务器启动时运行的生命周期函数。"""
    global idle_monitor_thread, last_activity_time, main_event_loop
    main_event_loop = asyncio.get_running_loop()  # 获取主事件循环
    load_config()  # 首先加载配置

    # --- 打印当前的操作模式 ---
    mode = CONFIG.get("id_updater_last_mode", "direct_chat")
    target = CONFIG.get("id_updater_battle_target", "A")
    logger.info("=" * 60)
    logger.info(f"  当前操作模式: {mode.upper()}")
    if mode == 'battle':
        logger.info(f"  - Battle 模式目标: Assistant {target}")
    logger.info("  (可通过运行 id_updater.py 修改模式)")
    logger.info("=" * 60)

    check_for_updates()  # 检查程序更新
    load_model_map()  # 重新启用模型加载
    load_model_endpoint_map()  # 加载模型端点映射
    logger.info("服务器启动完成。等待油猴脚本连接...")

    # 在模型更新后，标记活动时间的起点
    last_activity_time = datetime.now()

    # 启动空闲监控线程
    if CONFIG.get("enable_idle_restart", False):
        idle_monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
        idle_monitor_thread.start()

    yield
    logger.info("服务器正在关闭。")


app = FastAPI(lifespan=lifespan)

# --- CORS 中间件配置 ---
# 允许所有来源、所有方法、所有请求头，这对于本地开发工具是安全的。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 辅助函数 ---
def save_config():
    """将当前的 CONFIG 对象写回 config.jsonc 文件，保留注释。"""
    try:
        # 读取原始文件以保留注释等
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # 使用正则表达式安全地替换值
        def replacer(key, value, content):
            # 这个正则表达式会找到 key，然后匹配它的 value 部分，直到逗号或右花括号
            pattern = re.compile(rf'("{key}"\s*:\s*").*?("?)(,?\s*)$', re.MULTILINE)
            replacement = rf'\g<1>{value}\g<2>\g<3>'
            if not pattern.search(content):  # 如果 key 不存在，就添加到文件末尾（简化处理）
                content = re.sub(r'}\s*$', f'  ,"{key}": "{value}"\n}}', content)
            else:
                content = pattern.sub(replacement, content)
            return content

        content_str = "".join(lines)
        content_str = replacer("session_id", CONFIG["session_id"], content_str)
        content_str = replacer("message_id", CONFIG["message_id"], content_str)

        with open('config.jsonc', 'w', encoding='utf-8') as f:
            f.write(content_str)
        logger.info("✅ 成功将会话信息更新到 config.jsonc。")
    except Exception as e:
        logger.error(f"❌ 写入 config.jsonc 时发生错误: {e}", exc_info=True)


def _process_openai_message(message: dict) -> dict:
    """
    处理OpenAI消息，分离文本和附件。
    - 将多模态内容列表分解为纯文本和附件列表。
    - 确保 user 角色的空内容被替换为空格，以避免 LMArena 出错。
    - 为附件生成基础结构。
    """
    content = message.get("content")
    role = message.get("role")
    attachments = []
    text_content = ""

    if isinstance(content, list):

        text_parts = []
        for part in content:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                image_url_data = part.get("image_url", {})
                url = image_url_data.get("url")

                # 新增逻辑：允许客户端通过 detail 字段传递原始文件名
                # detail 字段是 OpenAI Vision API 的一部分，这里我们复用它
                original_filename = image_url_data.get("detail")

                if url and url.startswith("data:"):
                    try:
                        content_type = url.split(';')[0].split(':')[1]

                        # 如果客户端提供了原始文件名，直接使用它
                        if original_filename and isinstance(original_filename, str):
                            file_name = original_filename
                            logger.info(f"成功处理一个附件 (使用原始文件名): {file_name}")
                        else:
                            # 否则，回退到旧的、基于UUID的命名逻辑
                            main_type, sub_type = content_type.split('/') if '/' in content_type else (
                            'application', 'octet-stream')

                            if main_type == "image":
                                prefix = "image"
                            elif main_type == "audio":
                                prefix = "audio"
                            else:
                                prefix = "file"

                            guessed_extension = mimetypes.guess_extension(content_type)
                            if guessed_extension:
                                file_extension = guessed_extension.lstrip('.')
                            else:
                                file_extension = sub_type if len(sub_type) < 20 else 'bin'

                            file_name = f"{prefix}_{uuid.uuid4()}.{file_extension}"
                            logger.info(f"成功处理一个附件 (生成文件名): {file_name}")

                        attachments.append({
                            "name": file_name,
                            "contentType": content_type,
                            "url": url
                        })
                    except (IndexError, ValueError) as e:
                        logger.warning(f"无法解析的 base64 data URI: {url[:60]}... 错误: {e}")

        text_content = "\n\n".join(text_parts)
    elif isinstance(content, str):
        text_content = content

    if role == "user" and not text_content.strip():
        text_content = " "

    return {
        "role": role,
        "content": text_content,
        "attachments": attachments
    }


def convert_openai_to_lmarena_payload(openai_data: dict, session_id: str, message_id: str, mode_override: str = None,
                                      battle_target_override: str = None) -> dict:
    """
    将 OpenAI 请求体转换为油猴脚本所需的简化载荷，并应用酒馆模式、绕过模式以及对战模式。
    新增了模式覆盖参数，以支持模型特定的会话模式。
    """
    # 1. 规范化角色并处理消息
    #    - 将非标准的 'developer' 角色转换为 'system' 以提高兼容性。
    #    - 分离文本和附件。
    messages = openai_data.get("messages", [])
    for msg in messages:
        if msg.get("role") == "developer":
            msg["role"] = "system"
            logger.info("消息角色规范化：将 'developer' 转换为 'system'。")

    processed_messages = [_process_openai_message(msg.copy()) for msg in messages]

    # 2. 应用酒馆模式 (Tavern Mode)
    if CONFIG.get("tavern_mode_enabled"):
        system_prompts = [msg['content'] for msg in processed_messages if msg['role'] == 'system']
        other_messages = [msg for msg in processed_messages if msg['role'] != 'system']

        merged_system_prompt = "\n\n".join(system_prompts)
        final_messages = []

        if merged_system_prompt:
            # 系统消息不应有附件
            final_messages.append({"role": "system", "content": merged_system_prompt, "attachments": []})

        final_messages.extend(other_messages)
        processed_messages = final_messages

    # 3. 确定目标模型 ID
    model_name = openai_data.get("model", "claude-3-5-sonnet-20241022")
    model_info = MODEL_NAME_TO_ID_MAP.get(model_name, {})  # 关键修复：确保 model_info 总是一个字典

    target_model_id = None
    if model_info:
        target_model_id = model_info.get("id")
    else:
        logger.warning(f"模型 '{model_name}' 在 'models.json' 中未找到。请求将不带特定模型ID发送。")

    if not target_model_id:
        logger.warning(f"模型 '{model_name}' 在 'models.json' 中未找到对应的ID。请求将不带特定模型ID发送。")

    # 4. 构建消息模板
    message_templates = []
    for msg in processed_messages:
        message_templates.append({
            "role": msg["role"],
            "content": msg.get("content", ""),
            "attachments": msg.get("attachments", [])
        })

    # 5. 应用绕过模式 (Bypass Mode) - 仅对文本模型生效
    model_type = model_info.get("type", "text")
    if CONFIG.get("bypass_enabled") and model_type == "text":
        # 绕过模式总是添加一个 position 'a' 的用户消息
        logger.info("绕过模式已启用，正在注入一个空的用户消息。")
        message_templates.append({"role": "user", "content": " ", "participantPosition": "a", "attachments": []})

    # 6. 应用参与者位置 (Participant Position)
    # 优先使用覆盖的模式，否则回退到全局配置
    mode = mode_override or CONFIG.get("id_updater_last_mode", "direct_chat")
    target_participant = battle_target_override or CONFIG.get("id_updater_battle_target", "A")
    target_participant = target_participant.lower()  # 确保是小写

    logger.info(
        f"正在根据模式 '{mode}' (目标: {target_participant if mode == 'battle' else 'N/A'}) 设置 Participant Positions...")

    for msg in message_templates:
        if msg['role'] == 'system':
            if mode == 'battle':
                # Battle 模式: system 与用户选择的助手在同一边 (A则a, B则b)
                msg['participantPosition'] = target_participant
            else:
                # DirectChat 模式: system 固定为 'b'
                msg['participantPosition'] = 'b'
        elif mode == 'battle':
            # Battle 模式下，非 system 消息使用用户选择的目标 participant
            msg['participantPosition'] = target_participant
        else:  # DirectChat 模式
            # DirectChat 模式下，非 system 消息使用默认的 'a'
            msg['participantPosition'] = 'a'

    return {
        "message_templates": message_templates,
        "target_model_id": target_model_id,
        "session_id": session_id,
        "message_id": message_id
    }


# --- OpenAI 格式化辅助函数 (确保JSON序列化稳健) ---
def format_openai_chunk(content: str, model: str, request_id: str) -> str:
    """格式化为 OpenAI 流式块。"""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def format_openai_finish_chunk(model: str, request_id: str, reason: str = 'stop') -> str:
    """格式化为 OpenAI 结束块。"""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"


def format_openai_error_chunk(error_message: str, model: str, request_id: str) -> str:
    """格式化为 OpenAI 错误块。"""
    content = f"\n\n[LMArena Bridge Error]: {error_message}"
    return format_openai_chunk(content, model, request_id)


def format_openai_non_stream_response(content: str, model: str, request_id: str, reason: str = 'stop') -> dict:
    """构建符合 OpenAI 规范的非流式响应体。"""
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": reason,
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content) // 4,
            "total_tokens": len(content) // 4,
        },
    }


async def _process_lmarena_stream(request_id: str):
    """
    核心内部生成器：处理来自浏览器的原始数据流，并产生结构化事件。
    事件类型: ('content', str), ('finish', str), ('error', str)
    """
    channel_info = response_channels.get(request_id)
    if not channel_info:
        logger.error(f"PROCESSOR [ID: {request_id[:8]}]: 无法找到响应通道。")
        yield 'error', 'Internal server error: response channel not found.'
        return
    queue = channel_info["queue"]
    if not queue:
        logger.error(f"PROCESSOR [ID: {request_id[:8]}]: 无法找到响应通道。")
        yield 'error', 'Internal server error: response channel not found.'
        return

    buffer = ""
    timeout = CONFIG.get("stream_response_timeout_seconds", 360)
    text_pattern = re.compile(r'[ab]0:"((?:\\.|[^"\\])*)"')
    # 新增：用于匹配和提取图片URL的正则表达式
    image_pattern = re.compile(r'[ab]2:(\[.*?\])')
    finish_pattern = re.compile(r'[ab]d:(\{.*?"finishReason".*?\})')
    error_pattern = re.compile(r'(\{\s*"error".*?\})', re.DOTALL)
    cloudflare_patterns = [r'<title>Just a moment...</title>', r'Enable JavaScript and cookies to continue']

    try:
        while True:
            try:
                raw_data = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"PROCESSOR [ID: {request_id[:8]}]: 等待浏览器数据超时（{timeout}秒）。")
                yield 'error', f'Response timed out after {timeout} seconds.'
                return

            # 1. 检查来自 WebSocket 端的直接错误或终止信号
            if isinstance(raw_data, dict) and 'error' in raw_data:
                error_msg = raw_data.get('error', 'Unknown browser error')

                # 增强错误处理
                if isinstance(error_msg, str):
                    # 1. 检查 413 附件过大错误
                    if '413' in error_msg or 'too large' in error_msg.lower():
                        friendly_error_msg = "上传失败：附件大小超过了 LMArena 服务器的限制 (通常是 5MB左右)。请尝试压缩文件或上传更小的文件。"
                        logger.warning(f"PROCESSOR [ID: {request_id[:8]}]: 检测到附件过大错误 (413)。")
                        yield 'error', friendly_error_msg
                        return

                    # 2. 检查 Cloudflare 验证页面
                    if any(re.search(p, error_msg, re.IGNORECASE) for p in cloudflare_patterns):
                        friendly_error_msg = "检测到 Cloudflare 人机验证页面。请在浏览器中刷新 LMArena 页面并手动完成验证，然后重试请求。"
                        if browser_ws:
                            try:
                                await browser_ws.send_text(json.dumps({"command": "refresh"}, ensure_ascii=False))
                                logger.info(f"PROCESSOR [ID: {request_id[:8]}]: 在错误消息中检测到CF并已发送刷新指令。")
                            except Exception as e:
                                logger.error(f"PROCESSOR [ID: {request_id[:8]}]: 发送刷新指令失败: {e}")
                        yield 'error', friendly_error_msg
                        return

                # 3. 其他未知错误
                yield 'error', error_msg
                return
            if raw_data == "[DONE]":
                break

            buffer += "".join(str(item) for item in raw_data) if isinstance(raw_data, list) else raw_data

            if any(re.search(p, buffer, re.IGNORECASE) for p in cloudflare_patterns):
                error_msg = "检测到 Cloudflare 人机验证页面。请在浏览器中刷新 LMArena 页面并手动完成验证，然后重试请求。"
                if browser_ws:
                    try:
                        await browser_ws.send_text(json.dumps({"command": "refresh"}, ensure_ascii=False))
                        logger.info(f"PROCESSOR [ID: {request_id[:8]}]: 已向浏览器发送页面刷新指令。")
                    except Exception as e:
                        logger.error(f"PROCESSOR [ID: {request_id[:8]}]: 发送刷新指令失败: {e}")
                yield 'error', error_msg
                return

            if (error_match := error_pattern.search(buffer)):
                try:
                    error_json = json.loads(error_match.group(1))
                    yield 'error', error_json.get("error", "来自 LMArena 的未知错误")
                    return
                except json.JSONDecodeError:
                    pass

            # 优先处理文本内容
            while (match := text_pattern.search(buffer)):
                try:
                    text_content = json.loads(f'"{match.group(1)}"')
                    if text_content: yield 'content', text_content
                except (ValueError, json.JSONDecodeError):
                    pass
                buffer = buffer[match.end():]

            # 新增：处理图片内容
            while (match := image_pattern.search(buffer)):
                try:
                    image_data_list = json.loads(match.group(1))
                    if isinstance(image_data_list, list) and image_data_list:
                        image_info = image_data_list[0]
                        if image_info.get("type") == "image" and "image" in image_info:
                            # 将URL包装成Markdown格式并作为内容块yield
                            markdown_image = f"![Image]({image_info['image']})"
                            yield 'content', markdown_image
                except (json.JSONDecodeError, IndexError) as e:
                    logger.warning(f"解析图片URL时出错: {e}, buffer: {buffer[:150]}")
                buffer = buffer[match.end():]

            if (finish_match := finish_pattern.search(buffer)):
                try:
                    finish_data = json.loads(finish_match.group(1))
                    yield 'finish', finish_data.get("finishReason", "stop")
                except (json.JSONDecodeError, IndexError):
                    pass
                buffer = buffer[finish_match.end():]

    except asyncio.CancelledError:
        logger.info(f"PROCESSOR [ID: {request_id[:8]}]: 任务被取消。")
    finally:
        if request_id in response_channels:
            del response_channels[request_id]
            logger.info(f"PROCESSOR [ID: {request_id[:8]}]: 响应通道已清理。")


async def stream_generator(request_id: str, model: str):
    """将内部事件流格式化为 OpenAI SSE 响应。"""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    logger.info(f"STREAMER [ID: {request_id[:8]}]: 流式生成器启动。")

    finish_reason_to_send = 'stop'  # 默认的结束原因

    async for event_type, data in _process_lmarena_stream(request_id):
        if event_type == 'content':
            yield format_openai_chunk(data, model, response_id)
        elif event_type == 'finish':
            # 记录结束原因，但不要立即返回，等待浏览器发送 [DONE]
            finish_reason_to_send = data
            if data == 'content-filter':
                warning_msg = "\n\n响应被终止，可能是上下文超限或者模型内部审查（大概率）的原因"
                yield format_openai_chunk(warning_msg, model, response_id)
        elif event_type == 'error':
            logger.error(f"STREAMER [ID: {request_id[:8]}]: 流中发生错误: {data}")
            yield format_openai_error_chunk(str(data), model, response_id)
            yield format_openai_finish_chunk(model, response_id, reason='stop')
            return  # 发生错误时，可以立即终止

    # 只有在 _process_lmarena_stream 自然结束后 (即收到 [DONE]) 才执行
    yield format_openai_finish_chunk(model, response_id, reason=finish_reason_to_send)
    logger.info(f"STREAMER [ID: {request_id[:8]}]: 流式生成器正常结束。")


async def non_stream_response(request_id: str, model: str):
    """聚合内部事件流并返回单个 OpenAI JSON 响应。"""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    logger.info(f"NON-STREAM [ID: {request_id[:8]}]: 开始处理非流式响应。")

    full_content = []
    finish_reason = "stop"

    async for event_type, data in _process_lmarena_stream(request_id):
        if event_type == 'content':
            full_content.append(data)
        elif event_type == 'finish':
            finish_reason = data
            if data == 'content-filter':
                full_content.append("\n\n响应被终止，可能是上下文超限或者模型内部审查（大概率）的原因")
            # 不要在这里 break，继续等待来自浏览器的 [DONE] 信号，以避免竞态条件
        elif event_type == 'error':
            logger.error(f"NON-STREAM [ID: {request_id[:8]}]: 处理时发生错误: {data}")

            # 统一流式和非流式响应的错误状态码
            status_code = 413 if "附件大小超过了" in str(data) else 500

            error_response = {
                "error": {
                    "message": f"[LMArena Bridge Error]: {data}",
                    "type": "bridge_error",
                    "code": "attachment_too_large" if status_code == 413 else "processing_error"
                }
            }
            return Response(content=json.dumps(error_response, ensure_ascii=False), status_code=status_code,
                            media_type="application/json")

    final_content = "".join(full_content)
    response_data = format_openai_non_stream_response(final_content, model, response_id, reason=finish_reason)

    logger.info(f"NON-STREAM [ID: {request_id[:8]}]: 响应聚合完成。")
    return Response(content=json.dumps(response_data, ensure_ascii=False), media_type="application/json")


# --- WebSocket 端点 ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """处理来自油猴脚本的 WebSocket 连接。"""
    global browser_ws
    await websocket.accept()
    if browser_ws is not None:
        logger.warning("检测到新的油猴脚本连接，旧的连接将被替换。")
    logger.info("✅ 油猴脚本已成功连接 WebSocket。")
    browser_ws = websocket
    try:
        while True:
            message_str = await websocket.receive_text()
            message = json.loads(message_str)

            request_id = message.get("request_id")

            # --- 新增代码从这里开始 ---
            if message.get("status") == "ready_to_click" and request_id in response_channels:
                logger.info(f"API CALL [ID: {request_id[:8]}]: 收到前端 'ready_to_click' 信号。")
                response_channels[request_id]["event"].set()  # 触发事件，通知 API 端点可以继续执行
                continue  # 处理完信号后，继续等待下一条消息 (可能是数据流)

            # 下面是原有的数据处理逻辑，保持不变
            data = message.get("data")

            if not request_id or data is None:
                logger.warning(f"收到来自浏览器的无效消息: {message}")
                continue

            # 将收到的数据放入对应的响应通道
            if request_id in response_channels:
                await response_channels[request_id]["queue"].put(data)
            else:
                logger.warning(f"⚠️ 收到未知或已关闭请求的响应: {request_id}")

    except WebSocketDisconnect:
        logger.warning("❌ 油猴脚本客户端已断开连接。")
    except Exception as e:
        logger.error(f"WebSocket 处理时发生未知错误: {e}", exc_info=True)
    finally:
        browser_ws = None
        # 清理所有等待的响应通道，以防请求被挂起
        for channel_info in response_channels.values():
            await channel_info["queue"].put({"error": "Browser disconnected during operation"})
        response_channels.clear()
        logger.info("WebSocket 连接已清理。")


# --- OpenAI 兼容 API 端点 ---
@app.get("/v1/models")
async def get_models():
    """提供兼容 OpenAI 的模型列表。"""
    if not MODEL_NAME_TO_ID_MAP:
        return JSONResponse(
            status_code=404,
            content={"error": "模型列表为空或 'models.json' 未找到。"}
        )

    return {
        "object": "list",
        "data": [
            {
                "id": model_name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "LMArenaBridge"
            }
            for model_name in MODEL_NAME_TO_ID_MAP.keys()
        ],
    }


@app.post("/internal/request_model_update")
async def request_model_update():
    """
    接收来自 model_updater.py 的请求，并通过 WebSocket 指令
    让油猴脚本发送页面源码。
    """
    if not browser_ws:
        logger.warning("MODEL UPDATE: 收到更新请求，但没有浏览器连接。")
        raise HTTPException(status_code=503, detail="Browser client not connected.")

    try:
        logger.info("MODEL UPDATE: 收到更新请求，正在通过 WebSocket 发送指令...")
        await browser_ws.send_text(json.dumps({"command": "send_page_source"}))
        logger.info("MODEL UPDATE: 'send_page_source' 指令已成功发送。")
        return JSONResponse({"status": "success", "message": "Request to send page source sent."})
    except Exception as e:
        logger.error(f"MODEL UPDATE: 发送指令时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send command via WebSocket.")


@app.post("/internal/update_available_models")
async def update_available_models_endpoint(request: Request):
    """
    接收来自油猴脚本的页面 HTML，提取并更新 available_models.json。
    """
    html_content = await request.body()
    if not html_content:
        logger.warning("模型更新请求未收到任何 HTML 内容。")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No HTML content received."}
        )

    logger.info("收到来自油猴脚本的页面内容，开始提取可用模型...")
    new_models_list = extract_models_from_html(html_content.decode('utf-8'))

    if new_models_list:
        save_available_models(new_models_list)
        return JSONResponse({"status": "success", "message": "Available models file updated."})
    else:
        logger.error("未能从油猴脚本提供的 HTML 中提取模型数据。")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Could not extract model data from HTML."}
        )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    处理聊天补全请求。
    接收 OpenAI 格式的请求，将其转换为 LMArena 格式，
    通过 WebSocket 发送给油猴脚本，然后流式返回结果。
    """
    global last_activity_time
    last_activity_time = datetime.now()  # 更新活动时间
    logger.info(f"API请求已收到，活动时间已更新为: {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        openai_req = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON 请求体")

    model_name = openai_req.get("model")
    model_info = MODEL_NAME_TO_ID_MAP.get(model_name, {})  # 关键修复：如果模型未找到，返回一个空字典而不是None
    model_type = model_info.get("type", "text")  # 默认为 text

    # --- 新增：基于模型类型的判断逻辑 ---
    if model_type == 'image':
        logger.info(f"检测到模型 '{model_name}' 类型为 'image'，将通过主聊天接口处理。")
        # 对于图像模型，我们不再调用独立的处理器，而是复用主聊天逻辑，
        # 因为 _process_lmarena_stream 现在已经能处理图片数据。
        # 这意味着图像生成现在原生支持流式和非流式响应。
        pass  # 继续执行下面的通用聊天逻辑
    # --- 文生图逻辑结束 ---

    # 如果不是图像模型，则执行正常的文本生成逻辑
    load_config()  # 实时加载最新配置，确保会话ID等信息是最新的
    # --- API Key 验证 ---
    api_key = CONFIG.get("api_key")
    if api_key:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            raise HTTPException(
                status_code=401,
                detail="未提供 API Key。请在 Authorization 头部中以 'Bearer YOUR_KEY' 格式提供。"
            )

        provided_key = auth_header.split(' ')[1]
        if provided_key != api_key:
            raise HTTPException(
                status_code=401,
                detail="提供的 API Key 不正确。"
            )

    if not browser_ws:
        raise HTTPException(status_code=503, detail="油猴脚本客户端未连接。请确保 LMArena 页面已打开并激活脚本。")

    # --- 模型与会话ID映射逻辑 ---
    session_id, message_id = None, None
    mode_override, battle_target_override = None, None

    if model_name and model_name in MODEL_ENDPOINT_MAP:
        mapping_entry = MODEL_ENDPOINT_MAP[model_name]
        selected_mapping = None

        if isinstance(mapping_entry, list) and mapping_entry:
            selected_mapping = random.choice(mapping_entry)
            logger.info(f"为模型 '{model_name}' 从ID列表中随机选择了一个映射。")
        elif isinstance(mapping_entry, dict):
            selected_mapping = mapping_entry
            logger.info(f"为模型 '{model_name}' 找到了单个端点映射（旧格式）。")

        if selected_mapping:
            session_id = selected_mapping.get("session_id")
            message_id = selected_mapping.get("message_id")
            # 关键：同时获取模式信息
            mode_override = selected_mapping.get("mode")  # 可能为 None
            battle_target_override = selected_mapping.get("battle_target")  # 可能为 None
            log_msg = f"将使用 Session ID: ...{session_id[-6:] if session_id else 'N/A'}"
            if mode_override:
                log_msg += f" (模式: {mode_override}"
                if mode_override == 'battle':
                    log_msg += f", 目标: {battle_target_override or 'A'}"
                log_msg += ")"
            logger.info(log_msg)

    # 如果经过以上处理，session_id 仍然是 None，则进入全局回退逻辑
    if not session_id:
        if CONFIG.get("use_default_ids_if_mapping_not_found", True):
            session_id = CONFIG.get("session_id")
            message_id = CONFIG.get("message_id")
            # 当使用全局ID时，不设置模式覆盖，让其使用全局配置
            mode_override, battle_target_override = None, None
            logger.info(
                f"模型 '{model_name}' 未找到有效映射，根据配置使用全局默认 Session ID: ...{session_id[-6:] if session_id else 'N/A'}")
        else:
            logger.error(f"模型 '{model_name}' 未在 'model_endpoint_map.json' 中找到有效映射，且已禁用回退到默认ID。")
            raise HTTPException(
                status_code=400,
                detail=f"模型 '{model_name}' 没有配置独立的会话ID。请在 'model_endpoint_map.json' 中添加有效映射或在 'config.jsonc' 中启用 'use_default_ids_if_mapping_not_found'。"
            )

    # --- 验证最终确定的会话信息 ---
    if not session_id or not message_id or "YOUR_" in session_id or "YOUR_" in message_id:
        raise HTTPException(
            status_code=400,
            detail="最终确定的会话ID或消息ID无效。请检查 'model_endpoint_map.json' 和 'config.jsonc' 中的配置，或运行 `id_updater.py` 来更新默认值。"
        )

    if not model_name or model_name not in MODEL_NAME_TO_ID_MAP:
        logger.warning(f"请求的模型 '{model_name}' 不在 models.json 中，将使用默认模型ID。")

    request_id = str(uuid.uuid4())
    # 创建包含 Queue 和 Event 的新结构
    response_channels[request_id] = {
        "queue": asyncio.Queue(),
        "event": asyncio.Event()
    }
    logger.info(f"API CALL [ID: {request_id[:8]}]: 已创建响应通道和同步事件。")

    try:
        # 1. 转换请求，传入可能存在的模式覆盖信息
        lmarena_payload = convert_openai_to_lmarena_payload(
            openai_req,
            session_id,
            message_id,
            mode_override=mode_override,
            battle_target_override=battle_target_override
        )

        # 2. 包装成发送给浏览器的消息
        message_to_browser = {
            "request_id": request_id,
            "payload": lmarena_payload
        }

        # 3. 通过 WebSocket 发送
        logger.info(f"API CALL [ID: {request_id[:8]}]: 正在通过 WebSocket 发送载荷到油猴脚本。")
        await browser_ws.send_text(json.dumps(message_to_browser))

        # 4. 等待前端准备就绪的信号，然后执行GUI点击
        try:
            # 等待油猴脚本发回 'ready_to_click' 信号，设置10秒超时
            await asyncio.wait_for(response_channels[request_id]["event"].wait(), timeout=10.0)

            # 收到信号，执行 clicker.py
            logger.info(f"API CALL [ID: {request_id[:8]}]: 前端准备就绪，正在执行 GUI 点击...")
            # 使用 sys.executable 确保调用的是当前环境的python解释器
            subprocess.run([sys.executable, 'clicker.py'], check=True)
            logger.info(f"API CALL [ID: {request_id[:8]}]: GUI 点击已成功执行。")

        except asyncio.TimeoutError:
            logger.error(f"API CALL [ID: {request_id[:8]}]: 等待前端 'ready_to_click' 信号超时。")
            raise HTTPException(status_code=504, detail="前端自动化准备超时，请检查浏览器窗口是否正常。")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"API CALL [ID: {request_id[:8]}]: 执行 clicker.py 失败: {e}")
            raise HTTPException(status_code=500, detail="执行GUI自动化点击脚本失败，请检查clicker.py是否存在且坐标正确。")

        # 4. 根据 stream 参数决定返回类型
        is_stream = openai_req.get("stream", False)

        if is_stream:
            # 返回流式响应
            return StreamingResponse(
                stream_generator(request_id, model_name or "default_model"),
                media_type="text/event-stream"
            )
        else:
            # 返回非流式响应
            return await non_stream_response(request_id, model_name or "default_model")
    except Exception as e:
        # 如果在设置过程中出错，清理通道
        if request_id in response_channels:
            del response_channels[request_id]
        logger.error(f"API CALL [ID: {request_id[:8]}]: 处理请求时发生致命错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- 内部通信端点 ---
@app.post("/internal/start_id_capture")
async def start_id_capture():
    """
    接收来自 id_updater.py 的通知，并通过 WebSocket 指令
    激活油猴脚本的 ID 捕获模式。
    """
    if not browser_ws:
        logger.warning("ID CAPTURE: 收到激活请求，但没有浏览器连接。")
        raise HTTPException(status_code=503, detail="Browser client not connected.")

    try:
        logger.info("ID CAPTURE: 收到激活请求，正在通过 WebSocket 发送指令...")
        await browser_ws.send_text(json.dumps({"command": "activate_id_capture"}))
        logger.info("ID CAPTURE: 激活指令已成功发送。")
        return JSONResponse({"status": "success", "message": "Activation command sent."})
    except Exception as e:
        logger.error(f"ID CAPTURE: 发送激活指令时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send command via WebSocket.")


# --- 主程序入口 ---
if __name__ == "__main__":
    # 建议从 config.jsonc 中读取端口，此处为临时硬编码
    api_port = 5102
    logger.info(f"🚀 LMArena Bridge v2.0 API 服务器正在启动...")
    logger.info(f"   - 监听地址: http://127.0.0.1:{api_port}")
    logger.info(f"   - WebSocket 端点: ws://127.0.0.1:{api_port}/ws")

    uvicorn.run(app, host="0.0.0.0", port=api_port)
