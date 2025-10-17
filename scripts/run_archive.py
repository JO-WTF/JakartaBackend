import os
import sys
from datetime import datetime

# 自动加载 env.test 环境变量
def load_env_file(env_path):
    if not os.path.exists(env_path):
        print(f"[WARN] env file not found: {env_path}")
        return
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

load_env_file(os.path.join(os.path.dirname(__file__), '../env.test'))

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.api.dn.archive import archive_plan_mos

if __name__ == "__main__":
    print(f"[Archive] 开始归档: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    result = archive_plan_mos()
    print("归档结果：")
    print(result)
