import time
import subprocess
import os

def set_brightness(level):
    # استخدام PowerShell لتعديل سطوع الشاشة في ويندوز
    ps_command = "(Get-WmiObject -Namespace root\WMI -Class WmiMonitorBrightnesMethods).WmiSetBrightness(1, )"
    subprocess.run(["powershell", "-Command", ps_command], capture_output=True)

def main():
    print("SAIA_STEP: بدء تشغيل خدمة خفض السطوع التلقائية")
    while True:
        try:
            # مثال: خفض السطوع بمقدار معين أو ضبطه على مستوى منخفض
            # سنقوم بقراءة السطوع الحالي أو تخفيضه تدريجياً، أو تثبيته عند 30% مثلاً
            set_brightness(30)
            print("SAIA_STEP: تم تخفيض سطوع الشاشة بنجاح")
        except Exception as e:
            print(f"Error: {e}")
        
        # الانتظار لمدة 10 دقائق (600 ثانية)
        time.sleep(600)

if __name__ == "__main__":
    main()