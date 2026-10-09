import unittest

from web.auth import ClusterCredentials, CredentialStore


class CredentialStoreTests(unittest.TestCase):
    def test_create_get_and_delete(self) -> None:
        store = CredentialStore(ttl_seconds=60)
        credentials = ClusterCredentials("admin", "secret")
        token = store.create(credentials)

        self.assertEqual(store.get(token), credentials)
        store.delete(token)
        self.assertIsNone(store.get(token))

    def test_expired_session_is_not_returned(self) -> None:
        store = CredentialStore(ttl_seconds=0)
        token = store.create(ClusterCredentials("admin", "secret"))

        self.assertIsNone(store.get(token))


if __name__ == "__main__":
    unittest.main()
