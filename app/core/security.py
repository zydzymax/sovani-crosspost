"""
Security utilities for SalesWhisper Crosspost.

This module provides:
- AES-GCM encryption/decryption for sensitive data
- HMAC signature generation and verification for webhooks
- JWT token generation and validation
- Idempotency key generation and validation
- Password hashing and verification
"""

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.exceptions import InvalidSignature
import jwt
from passlib.context import CryptContext

from .config import settings


class EncryptionError(Exception):
    """Custom exception for encryption/decryption errors."""
    pass


class SignatureError(Exception):
    """Custom exception for signature verification errors."""
    pass


class AESGCMCipher:
    """AES-GCM encryption/decryption utility."""
    
    def __init__(self, key: Optional[bytes] = None):
        """Initialize cipher with key."""
        if key is None:
            key_str = settings.security.aes_key.get_secret_value()
            key = key_str.encode() if isinstance(key_str, str) else key_str
        
        if len(key) != 32:  # 256 bits
            raise ValueError("AES key must be exactly 32 bytes (256 bits)")
        
        self.cipher = AESGCM(key)
    
    def encrypt(self, data: str, associated_data: Optional[str] = None) -> str:
        """
        Encrypt data using AES-GCM.
        
        Args:
            data: String data to encrypt
            associated_data: Optional additional authenticated data
            
        Returns:
            Base64-encoded encrypted data with nonce
        """
        try:
            # Generate random nonce (12 bytes for GCM)
            nonce = secrets.token_bytes(12)
            
            # Prepare data
            plaintext = data.encode('utf-8')
            aad = associated_data.encode('utf-8') if associated_data else None
            
            # Encrypt
            ciphertext = self.cipher.encrypt(nonce, plaintext, aad)
            
            # Combine nonce + ciphertext and encode
            encrypted_data = nonce + ciphertext
            return base64.b64encode(encrypted_data).decode('ascii')
            
        except Exception as e:
            raise EncryptionError(f"Encryption failed: {str(e)}")
    
    def decrypt(self, encrypted_data: str, associated_data: Optional[str] = None) -> str:
        """
        Decrypt AES-GCM encrypted data.
        
        Args:
            encrypted_data: Base64-encoded encrypted data
            associated_data: Optional additional authenticated data
            
        Returns:
            Decrypted string data
        """
        try:
            # Decode from base64
            encrypted_bytes = base64.b64decode(encrypted_data)
            
            # Extract nonce (first 12 bytes) and ciphertext
            nonce = encrypted_bytes[:12]
            ciphertext = encrypted_bytes[12:]
            
            # Prepare associated data
            aad = associated_data.encode('utf-8') if associated_data else None
            
            # Decrypt
            plaintext = self.cipher.decrypt(nonce, ciphertext, aad)
            return plaintext.decode('utf-8')
            
        except Exception as e:
            raise EncryptionError(f"Decryption failed: {str(e)}")


class WebhookSigner:
    """HMAC-based webhook signature generation and verification."""
    
    def __init__(self, secret: Optional[str] = None):
        """Initialize signer with secret key."""
        self.secret = secret or settings.security.webhook_secret.get_secret_value()
        
    def sign(self, payload: str, timestamp: Optional[int] = None) -> str:
        """
        Generate HMAC signature for webhook payload.
        
        Args:
            payload: JSON string payload
            timestamp: Optional Unix timestamp (current time if None)
            
        Returns:
            Signature in format "t={timestamp},v1={signature}"
        """
        if timestamp is None:
            timestamp = int(time.time())
            
        # Create signed payload: timestamp.payload
        signed_payload = f"{timestamp}.{payload}"
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return f"t={timestamp},v1={signature}"
    
    def verify(self, payload: str, signature_header: str, tolerance: int = 300) -> bool:
        """
        Verify webhook signature.
        
        Args:
            payload: JSON string payload
            signature_header: Signature header from webhook
            tolerance: Maximum age of timestamp in seconds
            
        Returns:
            True if signature is valid and timestamp is within tolerance
        """
        try:
            # Parse signature header
            elements = signature_header.split(',')
            timestamp = None
            signatures = []
            
            for element in elements:
                if element.startswith('t='):
                    timestamp = int(element[2:])
                elif element.startswith('v1='):
                    signatures.append(element[3:])
            
            if timestamp is None or not signatures:
                return False
            
            # Check timestamp tolerance
            current_time = int(time.time())
            if current_time - timestamp > tolerance:
                return False
            
            # Generate expected signature
            signed_payload = f"{timestamp}.{payload}"
            expected_signature = hmac.new(
                self.secret.encode('utf-8'),
                signed_payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures using constant-time comparison
            for sig in signatures:
                if hmac.compare_digest(expected_signature, sig):
                    return True
            
            return False
            
        except (ValueError, TypeError):
            return False


class JWTManager:
    """JWT token generation and validation."""
    
    def __init__(self, secret: Optional[str] = None, algorithm: str = "HS256"):
        """Initialize JWT manager."""
        self.secret = secret or settings.security.jwt_secret_key.get_secret_value()
        self.algorithm = algorithm
    
    def create_token(self, payload: Dict[str, Any], 
                    expires_in: Optional[int] = None) -> str:
        """
        Create JWT token.
        
        Args:
            payload: Token payload data
            expires_in: Expiration time in seconds
            
        Returns:
            JWT token string
        """
        # Add standard claims
        now = datetime.utcnow()
        token_payload = {
            'iat': now,
            'jti': str(uuid.uuid4()),  # Unique token ID
            **payload
        }
        
        # Add expiration if specified
        if expires_in:
            token_payload['exp'] = now + timedelta(seconds=expires_in)
        
        return jwt.encode(token_payload, self.secret, algorithm=self.algorithm)
    
    def decode_token(self, token: str, verify_exp: bool = True) -> Dict[str, Any]:
        """
        Decode and validate JWT token.
        
        Args:
            token: JWT token string
            verify_exp: Whether to verify expiration
            
        Returns:
            Decoded payload
        """
        options = {"verify_exp": verify_exp}
        return jwt.decode(token, self.secret, algorithms=[self.algorithm], options=options)
    
    def create_api_key_token(self, user_id: str, scopes: list = None) -> str:
        """Create long-lived API key token."""
        payload = {
            'user_id': user_id,
            'type': 'api_key',
            'scopes': scopes or ['read', 'write']
        }
        # API keys don't expire by default
        return self.create_token(payload)
    
    def create_webhook_token(self, webhook_id: str, source: str) -> str:
        """Create webhook authentication token."""
        payload = {
            'webhook_id': webhook_id,
            'source': source,
            'type': 'webhook'
        }
        # Webhooks have 1-hour expiration
        return self.create_token(payload, expires_in=3600)


class IdempotencyManager:
    """Idempotency key generation and validation."""
    
    @staticmethod
    def generate_key(prefix: str = "", length: int = 16) -> str:
        """
        Generate idempotency key.
        
        Args:
            prefix: Optional prefix for the key
            length: Length of random part in bytes
            
        Returns:
            Idempotency key string
        """
        random_part = secrets.token_urlsafe(length)
        if prefix:
            return f"{prefix}_{random_part}"
        return random_part
    
    @staticmethod
    def generate_from_content(content: str, prefix: str = "") -> str:
        """
        Generate deterministic idempotency key from content.
        
        Args:
            content: Content to hash
            prefix: Optional prefix
            
        Returns:
            Deterministic idempotency key
        """
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
        if prefix:
            return f"{prefix}_{content_hash}"
        return content_hash
    
    @staticmethod
    def validate_key(key: str, max_length: int = 255) -> bool:
        """
        Validate idempotency key format.
        
        Args:
            key: Idempotency key to validate
            max_length: Maximum allowed length
            
        Returns:
            True if key is valid
        """
        if not key or len(key) > max_length:
            return False
        
        # Allow alphanumeric, underscore, hyphen
        allowed_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')
        return all(c in allowed_chars for c in key)


class PasswordManager:
    """Password hashing and verification using bcrypt."""
    
    def __init__(self):
        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    
    def hash_password(self, password: str) -> str:
        """Hash password using bcrypt."""
        return self.pwd_context.hash(password)
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password against hash."""
        return self.pwd_context.verify(plain_password, hashed_password)
    
    def needs_rehash(self, hashed_password: str) -> bool:
        """Check if password needs to be rehashed."""
        return self.pwd_context.needs_update(hashed_password)


class SecurityUtils:
    """Collection of security utility functions."""
    
    @staticmethod
    def generate_secret_key(length: int = 32) -> str:
        """Generate cryptographically secure secret key."""
        return secrets.token_urlsafe(length)
    
    @staticmethod
    def generate_salt(length: int = 16) -> bytes:
        """Generate random salt for key derivation."""
        return secrets.token_bytes(length)
    
    @staticmethod
    def derive_key(password: str, salt: bytes, length: int = 32, 
                  iterations: int = 100000) -> bytes:
        """Derive key from password using PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            iterations=iterations,
        )
        return kdf.derive(password.encode('utf-8'))
    
    @staticmethod
    def constant_time_compare(a: str, b: str) -> bool:
        """Compare strings in constant time to prevent timing attacks."""
        return hmac.compare_digest(a, b)
    
    @staticmethod
    def hash_content(content: str, algorithm: str = 'sha256') -> str:
        """Hash content using specified algorithm."""
        hasher = hashlib.new(algorithm)
        hasher.update(content.encode('utf-8'))
        return hasher.hexdigest()


# Global instances
cipher = AESGCMCipher()
webhook_signer = WebhookSigner()
jwt_manager = JWTManager()
password_manager = PasswordManager()


# Convenience functions
def encrypt_data(data: str, associated_data: Optional[str] = None) -> str:
    """Encrypt data using default cipher."""
    return cipher.encrypt(data, associated_data)


def decrypt_data(encrypted_data: str, associated_data: Optional[str] = None) -> str:
    """Decrypt data using default cipher."""
    return cipher.decrypt(encrypted_data, associated_data)


def sign_webhook(payload: str, timestamp: Optional[int] = None) -> str:
    """Sign webhook payload."""
    return webhook_signer.sign(payload, timestamp)


def verify_webhook(payload: str, signature: str, tolerance: int = 300) -> bool:
    """Verify webhook signature."""
    return webhook_signer.verify(payload, signature, tolerance)


def create_jwt_token(payload: Dict[str, Any], expires_in: Optional[int] = None) -> str:
    """Create JWT token."""
    return jwt_manager.create_token(payload, expires_in)


def decode_jwt_token(token: str, verify_exp: bool = True) -> Dict[str, Any]:
    """Decode JWT token."""
    return jwt_manager.decode_token(token, verify_exp)


def generate_idempotency_key(prefix: str = "") -> str:
    """Generate idempotency key."""
    return IdempotencyManager.generate_key(prefix)


def hash_password(password: str) -> str:
    """Hash password."""
    return password_manager.hash_password(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify password."""
    return password_manager.verify_password(password, hashed)


# Example usage and testing
if __name__ == "__main__":
    """Example usage of security utilities."""
    
    print("= Testing SalesWhisper Security System")
    
    # Test AES encryption
    print("\n1. Testing AES-GCM Encryption:")
    test_data = "Sensitive user token: abc123"
    encrypted = encrypt_data(test_data)
    decrypted = decrypt_data(encrypted)
    print(f"  Original: {test_data}")
    print(f"  Encrypted: {encrypted[:50]}...")
    print(f"  Decrypted: {decrypted}")
    print(f"   Encryption/Decryption: {'OK' if decrypted == test_data else 'FAILED'}")
    
    # Test webhook signing
    print("\n2. Testing Webhook Signatures:")
    webhook_payload = '{"event": "post_created", "data": {"id": "123"}}'
    signature = sign_webhook(webhook_payload)
    is_valid = verify_webhook(webhook_payload, signature)
    print(f"  Payload: {webhook_payload}")
    print(f"  Signature: {signature}")
    print(f"   Signature verification: {'OK' if is_valid else 'FAILED'}")
    
    # Test invalid signature
    invalid_payload = '{"event": "post_created", "data": {"id": "456"}}'
    is_invalid = verify_webhook(invalid_payload, signature)
    print(f"   Invalid signature rejection: {'OK' if not is_invalid else 'FAILED'}")
    
    # Test JWT tokens
    print("\n3. Testing JWT Tokens:")
    token_payload = {"user_id": "user_123", "role": "admin"}
    token = create_jwt_token(token_payload, expires_in=3600)
    decoded = decode_jwt_token(token)
    print(f"  Token payload: {token_payload}")
    print(f"  JWT token: {token[:50]}...")
    print(f"  Decoded: {decoded}")
    print(f"   JWT creation/validation: {'OK' if decoded['user_id'] == 'user_123' else 'FAILED'}")
    
    # Test idempotency keys
    print("\n4. Testing Idempotency Keys:")
    key1 = generate_idempotency_key("post")
    key2 = generate_idempotency_key("post")
    content_key = IdempotencyManager.generate_from_content("same content", "content")
    content_key2 = IdempotencyManager.generate_from_content("same content", "content")
    print(f"  Random key 1: {key1}")
    print(f"  Random key 2: {key2}")
    print(f"  Content key 1: {content_key}")
    print(f"  Content key 2: {content_key2}")
    print(f"   Random keys unique: {'OK' if key1 != key2 else 'FAILED'}")
    print(f"   Content keys deterministic: {'OK' if content_key == content_key2 else 'FAILED'}")
    
    # Test password hashing
    print("\n5. Testing Password Hashing:")
    password = "secure_password_123"
    hashed = hash_password(password)
    is_valid = verify_password(password, hashed)
    is_invalid = verify_password("wrong_password", hashed)
    print(f"  Password: {password}")
    print(f"  Hashed: {hashed[:50]}...")
    print(f"   Password verification: {'OK' if is_valid else 'FAILED'}")
    print(f"   Wrong password rejection: {'OK' if not is_invalid else 'FAILED'}")
    
    print("\n Security system test completed")


def get_test_security_config() -> Dict[str, Any]:
    """Get security configuration for testing."""
    return {
        'aes_key': 'test-key-32-bytes-long-for-aes256!',
        'webhook_secret': 'test-webhook-secret',
        'jwt_secret': 'test-jwt-secret-key'
    }