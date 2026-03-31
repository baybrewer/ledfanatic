"""Tests for API authentication."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.auth import get_auth_token, create_auth_dependency


class TestGetAuthToken:
  def test_get_auth_token_valid(self):
    config = {'auth': {'token': 'secret123'}}
    assert get_auth_token(config) == 'secret123'

  def test_get_auth_token_missing(self):
    config = {}
    assert get_auth_token(config) is None

  def test_get_auth_token_placeholder(self):
    config = {'auth': {'token': 'your-secret-token-here'}}
    assert get_auth_token(config) is None


class TestCreateAuthDependency:
  @pytest.mark.asyncio
  async def test_create_auth_dependency_rejects_no_token(self):
    """No token configured -> all requests rejected with 401."""
    config = {}
    dep = create_auth_dependency(config)
    with pytest.raises(HTTPException) as exc_info:
      await dep(credentials=None)
    assert exc_info.value.status_code == 401

  @pytest.mark.asyncio
  async def test_create_auth_dependency_rejects_wrong_token(self):
    """Wrong bearer token -> 401."""
    config = {'auth': {'token': 'correct-token'}}
    dep = create_auth_dependency(config)
    wrong_creds = HTTPAuthorizationCredentials(
      scheme="Bearer", credentials="wrong-token"
    )
    with pytest.raises(HTTPException) as exc_info:
      await dep(credentials=wrong_creds)
    assert exc_info.value.status_code == 401

  @pytest.mark.asyncio
  async def test_create_auth_dependency_accepts_valid(self):
    """Correct bearer token -> no exception."""
    config = {'auth': {'token': 'correct-token'}}
    dep = create_auth_dependency(config)
    valid_creds = HTTPAuthorizationCredentials(
      scheme="Bearer", credentials="correct-token"
    )
    # Should not raise
    result = await dep(credentials=valid_creds)
    assert result is None
