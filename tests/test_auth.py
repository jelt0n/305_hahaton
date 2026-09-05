from unittest.mock import patch
import pytest
from app import earth_engine


def test_user_login_uses_persistent_credentials(monkeypatch):
    monkeypatch.setattr(earth_engine, '_initialized', False)
    monkeypatch.setenv('EE_PROJECT', 'test-project')
    monkeypatch.delenv('EE_SERVICE_ACCOUNT_FILE', raising=False)
    with patch.object(earth_engine.ee, 'Initialize') as initialize, patch.object(earth_engine.ee.data, 'setDeadline'):
        earth_engine.initialize()
        initialize.assert_called_once_with(project='test-project')


def test_service_account_uses_explicit_credentials(monkeypatch):
    monkeypatch.setattr(earth_engine, '_initialized', False)
    monkeypatch.setenv('EE_PROJECT', 'test-project')
    monkeypatch.setenv('EE_SERVICE_ACCOUNT_FILE', 'service-key.json')
    credentials = object()
    with patch.object(earth_engine.ee, 'ServiceAccountCredentials', return_value=credentials) as factory, patch.object(earth_engine.ee, 'Initialize') as initialize, patch.object(earth_engine.ee.data, 'setDeadline'):
        earth_engine.initialize()
        factory.assert_called_once_with(email=None, key_file='service-key.json')
        initialize.assert_called_once_with(credentials=credentials, project='test-project')


def test_missing_project_has_actionable_error(monkeypatch):
    monkeypatch.setattr(earth_engine, '_initialized', False)
    monkeypatch.setenv('EE_PROJECT', 'my-farmland-project')
    monkeypatch.delenv('EE_SERVICE_ACCOUNT_FILE', raising=False)
    with patch.object(earth_engine.ee, 'Initialize', side_effect=Exception("Project 'projects/my-farmland-project' not found or deleted.")):
        with pytest.raises(earth_engine.EarthEngineUnavailable, match='Project ID'):
            earth_engine.initialize()
