"""Authentication module"""
import bcrypt
from typing import Optional, Tuple
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .config import config

security = HTTPBearer()

# 密钥类型常量
API_KEY_TYPE_NORMAL = "normal"      # 普通密钥，可访问所有账户
API_KEY_TYPE_PREMIUM = "premium"    # 高级密钥，仅可访问高级账户


class AuthManager:
    """Authentication manager"""

    @staticmethod
    def verify_api_key(api_key: str) -> bool:
        """Verify API key (normal or premium)"""
        if api_key == config.api_key:
            return True
        if config.premium_api_key and api_key == config.premium_api_key:
            return True
        return False

    @staticmethod
    def get_api_key_type(api_key: str) -> Optional[str]:
        """Get the type of API key

        Returns:
            API_KEY_TYPE_NORMAL: 普通密钥，可访问所有账户
            API_KEY_TYPE_PREMIUM: 高级密钥，仅可访问高级账户
            None: 无效密钥
        """
        if api_key == config.api_key:
            return API_KEY_TYPE_NORMAL
        if config.premium_api_key and api_key == config.premium_api_key:
            return API_KEY_TYPE_PREMIUM
        return None

    @staticmethod
    def verify_admin(username: str, password: str) -> bool:
        """Verify admin credentials"""
        # Compare with current config (which may be from database or config file)
        return username == config.admin_username and password == config.admin_password

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password"""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """Verify password"""
        return bcrypt.checkpw(password.encode(), hashed.encode())


async def verify_api_key_header(credentials: HTTPAuthorizationCredentials = Security(security)) -> Tuple[str, str]:
    """Verify API key from Authorization header

    Returns:
        Tuple of (api_key, key_type) where key_type is 'normal' or 'premium'
    """
    api_key = credentials.credentials
    key_type = AuthManager.get_api_key_type(api_key)
    if key_type is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key, key_type
