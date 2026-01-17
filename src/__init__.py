"""
ScriptAgent - AI剧本生成系统
"""

from .resource_loader import ResourceLoader, Character, Scene, Action
from .director_ai import DirectorAI
from .json_generator import ScriptJSONGenerator

__all__ = [
    'ResourceLoader',
    'Character',
    'Scene',
    'Action',
    'DirectorAI',
    'ScriptJSONGenerator'
]

__version__ = '1.0.0'

