import pyautogui
import sys

# 请替换为您在虚拟机全屏模式下获取的“发送”按钮坐标
SEND_BUTTON_X = 1383 
SEND_BUTTON_Y = 1317

try:
    pyautogui.click(SEND_BUTTON_X, SEND_BUTTON_Y)
    # print(f"成功点击坐标: ({SEND_BUTTON_X}, {SEND_BUTTON_Y})") # 在生产环境中可以注释掉
    sys.exit(0)
except Exception as e:
    # print(f"点击失败: {e}", file=sys.stderr) # 打印错误到标准错误流
    sys.exit(1)
