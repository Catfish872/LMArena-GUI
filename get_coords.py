import pyautogui
import time
print("请在5秒内将鼠标移动到目标位置...")
time.sleep(5)
x, y = pyautogui.position()
print(f"当前鼠标坐标: X={x}, Y={y}")
