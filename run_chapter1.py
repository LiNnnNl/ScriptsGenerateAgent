#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, '/Users/lin/Desktop/work_space/ScriptsGenerateAgent/backend')
os.environ['SSL_CERT_FILE'] = '/Library/Frameworks/Python.framework/Versions/3.13/lib/python3.13/site-packages/certifi/cacert.pem'

from dotenv import load_dotenv
load_dotenv('/Users/lin/Desktop/work_space/ScriptsGenerateAgent/backend/.env')

from src.resource_loader import ResourceLoader
from src.autogen_bridge import AutoGenStreamBridge
from src.autogen_pipeline import run_autogen_pipeline

request_data = {
    'custom_characters': [
        {'name': '陈屿', 'description': '指令长，男，稳重果断，负责指挥决策', 'gender': '男'},
        {'name': '林静', 'description': '数据分析师，女，干练专业，冷静', 'gender': '女'},
        {'name': '老赵', 'description': '资深船员，男，吊儿郎当但靠谱，口语化', 'gender': '男'},
        {'name': 'Echo', 'description': '多足维修机器人，蓝色传感器，机械音播报', 'gender': '机器人'},
    ],
    'scene_id': 'Space Station',
    'creative_idea': '深空七号空间站外部场景，寂静空旷的宇宙中空间站悬浮于黑暗之中。Echo多足机器人在舷窗外侧攀附维修，蓝色传感器微光闪烁扫描空间站外壁。镜头跟随Echo展示空间站全貌。氛围冷峻孤独科技感。角色：陈屿、林静、老赵、Echo。',
    'enable_cinematography': True,
}

resource_loader = ResourceLoader()
bridge = AutoGenStreamBridge()
bridge.run_in_thread(run_autogen_pipeline(bridge, resource_loader, request_data))

for line in bridge.flask_generator():
    if line.strip():
        try:
            event = json.loads(line)
            msg_type = event.get('type')
            if msg_type == 'log':
                print(f'[{event.get("level")}] {event.get("message")}')
            elif msg_type == 'success':
                print(f'SUCCESS: script={event.get("filename")}, actors={event.get("actors_profile_filename")}, cinematography={event.get("cinematography_filename")}')
                break
            elif msg_type == 'error':
                print(f'ERROR: {event.get("message")}')
                break
        except:
            print(line.rstrip())