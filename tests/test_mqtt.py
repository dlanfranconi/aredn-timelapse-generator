import unittest
from unittest.mock import MagicMock, patch

from fenetre.mqtt import MQTTManager


class TestMQTTManagerTLS(unittest.TestCase):
    def _make_manager(self, **mqtt_config_overrides):
        config = {
            "enabled": True,
            "host": "broker.local",
            "port": 8883,
        }
        config.update(mqtt_config_overrides)
        return MQTTManager("Test Deployment", config)

    @patch("paho.mqtt.client.Client")
    def test_tls_enabled_calls_tls_set(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        manager = self._make_manager(tls=True)
        result = manager._ensure_client()

        self.assertTrue(result)
        mock_client.tls_set.assert_called_once_with(ca_certs=None)
        mock_client.tls_insecure_set.assert_not_called()

    @patch("paho.mqtt.client.Client")
    def test_tls_with_ca_certs_path(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        manager = self._make_manager(tls=True, ca_certs="/etc/ssl/custom-ca.pem")
        manager._ensure_client()

        mock_client.tls_set.assert_called_once_with(ca_certs="/etc/ssl/custom-ca.pem")

    @patch("paho.mqtt.client.Client")
    def test_tls_insecure_calls_tls_insecure_set(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        manager = self._make_manager(tls=True, tls_insecure=True)
        manager._ensure_client()

        mock_client.tls_insecure_set.assert_called_once_with(True)

    @patch("paho.mqtt.client.Client")
    def test_tls_disabled_by_default_skips_tls_set(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        manager = self._make_manager()
        manager._ensure_client()

        mock_client.tls_set.assert_not_called()
        mock_client.tls_insecure_set.assert_not_called()

    @patch("paho.mqtt.client.Client")
    def test_tls_set_failure_disables_client_and_does_not_connect(
        self, mock_client_cls
    ):
        mock_client = MagicMock()
        mock_client.tls_set.side_effect = OSError("bad ca_certs path")
        mock_client_cls.return_value = mock_client

        manager = self._make_manager(tls=True, ca_certs="/does/not/exist.pem")
        result = manager._ensure_client()

        self.assertFalse(result)
        mock_client.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
