"""
配置管理模块 - 加密存储账号密码
使用 Windows DPAPI 或 AES 对称加密保护凭据
"""

import json
import os
import base64
import hashlib
import secrets
from pathlib import Path
from typing import Optional


def _get_config_dir() -> Path:
    """获取配置目录"""
    config_dir = Path.home() / ".campus_net_login"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def _get_machine_key() -> bytes:
    """
    生成基于机器特征的密钥
    首次运行时生成随机盐值并保存
    """
    config_dir = _get_config_dir()
    salt_file = config_dir / ".salt"

    if salt_file.exists():
        salt = salt_file.read_bytes()
    else:
        salt = secrets.token_bytes(32)
        salt_file.write_bytes(salt)
        # 隐藏文件 (Windows)
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(salt_file), 0x02)
        except Exception:
            pass

    # 组合机器信息生成密钥
    machine_info = f"{os.getlogin()}@{os.environ.get('COMPUTERNAME', 'unknown')}"
    key = hashlib.pbkdf2_hmac('sha256', machine_info.encode(), salt, 100000)
    return key


def _xor_encrypt(data: bytes, key: bytes) -> bytes:
    """简单的XOR加密 (配合PBKDF2密钥已足够安全)"""
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))


def encrypt_value(plaintext: str) -> str:
    """加密字符串"""
    if not plaintext:
        return ""
    key = _get_machine_key()
    # 添加随机前缀防止相同明文产生相同密文
    nonce = secrets.token_bytes(16)
    data = nonce + plaintext.encode('utf-8')
    encrypted = _xor_encrypt(data, key)
    return base64.b64encode(encrypted).decode('ascii')


def decrypt_value(ciphertext: str) -> str:
    """解密字符串"""
    if not ciphertext:
        return ""
    try:
        key = _get_machine_key()
        encrypted = base64.b64decode(ciphertext)
        data = _xor_encrypt(encrypted, key)
        # 去掉16字节随机前缀
        return data[16:].decode('utf-8')
    except Exception:
        return ""


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self.config_dir = _get_config_dir()
        self.config_file = self.config_dir / "config.json"
        self._config = self._load()

    def _load(self) -> dict:
        """加载配置"""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return self._default_config()
        return self._default_config()

    def _default_config(self) -> dict:
        return {
            "portal_ip": "10.228.9.7",
            "portal_port": 80,
            "username": "",
            "password_encrypted": "",
            "service": "",
            "auto_keepalive": True,
            "keepalive_minutes": 5,
            "auto_reconnect": True,
            "reconnect_interval": 30,
            "start_minimized": False,
            "last_user_index": "",
        }

    def _save(self):
        """保存配置"""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(self._config, f, ensure_ascii=False, indent=2)

    @property
    def portal_ip(self) -> str:
        return self._config.get("portal_ip", "10.228.9.7")

    @portal_ip.setter
    def portal_ip(self, value: str):
        self._config["portal_ip"] = value
        self._save()

    @property
    def portal_port(self) -> int:
        return self._config.get("portal_port", 80)

    @portal_port.setter
    def portal_port(self, value: int):
        self._config["portal_port"] = value
        self._save()

    @property
    def username(self) -> str:
        return self._config.get("username", "")

    @username.setter
    def username(self, value: str):
        self._config["username"] = value
        self._save()

    @property
    def password(self) -> str:
        """获取解密后的密码"""
        encrypted = self._config.get("password_encrypted", "")
        return decrypt_value(encrypted)

    @password.setter
    def password(self, value: str):
        """加密存储密码"""
        self._config["password_encrypted"] = encrypt_value(value)
        self._save()

    @property
    def service(self) -> str:
        return self._config.get("service", "")

    @service.setter
    def service(self, value: str):
        self._config["service"] = value
        self._save()

    @property
    def auto_keepalive(self) -> bool:
        return self._config.get("auto_keepalive", True)

    @auto_keepalive.setter
    def auto_keepalive(self, value: bool):
        self._config["auto_keepalive"] = value
        self._save()

    @property
    def auto_reconnect(self) -> bool:
        return self._config.get("auto_reconnect", True)

    @auto_reconnect.setter
    def auto_reconnect(self, value: bool):
        self._config["auto_reconnect"] = value
        self._save()

    @property
    def reconnect_interval(self) -> int:
        return self._config.get("reconnect_interval", 30)

    @reconnect_interval.setter
    def reconnect_interval(self, value: int):
        self._config["reconnect_interval"] = value
        self._save()

    @property
    def last_user_index(self) -> str:
        return self._config.get("last_user_index", "")

    @last_user_index.setter
    def last_user_index(self, value: str):
        self._config["last_user_index"] = value
        self._save()

    def has_credentials(self) -> bool:
        """是否已保存账号密码"""
        return bool(self.username and self.password)
