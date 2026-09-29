"""Guard the localsend_share Nextcloud app without a Nextcloud installation."""
import json
import re
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def read(path):
    return (ROOT / path).read_text(encoding='utf-8')


class InfoXmlTests(unittest.TestCase):
    def setUp(self):
        self.info = read('appinfo/info.xml')
        self.xml = ET.fromstring(self.info)

    def test_the_id_and_namespace_match_the_php_namespace(self):
        self.assertEqual(self.xml.findtext('id'), 'localsend_share')
        self.assertEqual(self.xml.findtext('namespace'), 'LocalSendShare')
        self.assertIn('namespace OCA\\LocalSendShare\\AppInfo;',
                      read('lib/AppInfo/Application.php'))

    def test_the_licence_is_spdx_agpl(self):
        self.assertEqual(self.xml.findtext('licence'), 'AGPL-3.0-or-later')
        self.assertTrue((ROOT / 'LICENSE').exists())

    def test_the_supported_versions_allow_the_current_releases(self):
        nextcloud = self.xml.find('dependencies/nextcloud')
        self.assertEqual(nextcloud.get('min-version'), '33')
        self.assertGreaterEqual(int(nextcloud.get('max-version')), 35)

    def test_the_store_metadata_is_complete(self):
        for tag in ('summary', 'description', 'bugs', 'repository'):
            self.assertTrue(self.xml.findtext(tag), tag)

    def test_the_admin_settings_are_declared(self):
        self.assertEqual(self.xml.findtext('settings/admin'),
                         'OCA\\LocalSendShare\\Settings\\Admin')
        self.assertEqual(self.xml.findtext('settings/admin-section'),
                         'OCA\\LocalSendShare\\Settings\\AdminSection')


class AppTests(unittest.TestCase):
    def test_the_files_script_is_registered_on_the_files_page(self):
        self.assertIn('LoadAdditionalScriptsEvent::class',
                      read('lib/AppInfo/Application.php'))
        self.assertIn("Util::addInitScript('localsend_share', 'localsend')",
                      read('lib/Listener/LoadAdditionalScripts.php'))

    def test_the_controller_proxies_devices_and_send(self):
        controller = read('lib/Controller/SendController.php')
        self.assertIn("#[FrontpageRoute(verb: 'GET', url: '/devices')]", controller)
        self.assertIn("#[FrontpageRoute(verb: 'POST', url: '/send')]", controller)
        self.assertIn("getAppValue('localsend_share', 'relay_url'", controller)
        self.assertIn("getAppValue('localsend_share', 'relay_token'", controller)
        self.assertIn("'X-Send-To' => $fingerprint", controller)
        self.assertIn("'Bearer '", controller)

    def test_non_admins_may_use_the_send_routes(self):
        controller = read('lib/Controller/SendController.php')
        self.assertIn('use OCP\\AppFramework\\Http\\Attribute\\NoAdminRequired;', controller)
        self.assertEqual(controller.count('#[NoAdminRequired]'), 2)

    def test_only_administrators_may_change_the_settings(self):
        controller = read('lib/Controller/SettingsController.php')
        self.assertIn('AuthorizedAdminSetting(settings: Admin::class)', controller)
        self.assertIn("linkToRoute('localsend_share.settings.save')",
                      read('lib/Settings/Admin.php'))
        self.assertIn('IDelegatedSettings', read('lib/Settings/Admin.php'))
        self.assertIn('name="requesttoken"', read('templates/admin.php'))

    def test_the_action_uses_the_files_context_signature(self):
        source = read('src/localsend.js')
        self.assertIn('enabled: ({ nodes })', source)
        self.assertIn('exec: async ({ nodes })', source)
        self.assertIn('FileType.File', source)

    def test_the_relay_calls_handle_local_addresses_and_stream_sizes(self):
        controller = read('lib/Controller/SendController.php')
        # 既定のNextcloudはLAN内アドレスへのHTTPを拒否する。devicesにも指定が要る。
        self.assertEqual(controller.count("'allow_local_address' => true"), 2)
        # サイズ不明のストリームはchunkedになり、中継の上限検査をすり抜ける。
        self.assertIn("'Content-Length' => (string)$node->getSize()", controller)
        self.assertIn("'http_errors' => false", controller)
        self.assertIn("$payload['error']", controller)


class BundleTests(unittest.TestCase):
    def test_the_bundle_registers_the_send_action(self):
        bundle = read('js/localsend.js')
        self.assertIn('registerFileAction', bundle)
        self.assertIn('/apps/localsend_share/devices', bundle)
        self.assertIn('/apps/localsend_share/send', bundle)
        self.assertIn('localsend-share', bundle)
        # Register into the same @nextcloud/files v4 registry as the core.
        self.assertIn('_nc_files_scope', bundle)
        self.assertIn('register:action', bundle)

    def test_the_package_does_not_pull_the_dialog_vue_tree(self):
        package = json.loads(read('package.json'))
        self.assertNotIn('@nextcloud/dialogs', package['dependencies'])
        self.assertEqual(package['dependencies']['@nextcloud/files'], '^4.0.0')
        self.assertFalse((ROOT / 'js/localsend.css').exists())


class TranslationTests(unittest.TestCase):
    def test_japanese_covers_the_visible_strings(self):
        translations = json.loads(read('l10n/ja.json'))['translations']
        for key in ('Send via LocalSend', 'Choose a device', 'Cancel',
                    'Relay URL', 'Relay token', 'Save'):
            self.assertIn(key, translations)
            self.assertTrue(translations[key])

    def test_the_files_action_translations_ship_as_javascript(self):
        # addInitScript() loads l10n/<lang>.js; the .json file only serves PHP.
        source = read('l10n/ja.js')
        match = re.fullmatch(
            r'OC\.L10N\.register\(\s*"localsend_share",\s*(\{.*\}),\s*"[^"]*"\s*\);\s*',
            source, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1)),
                         json.loads(read('l10n/ja.json'))['translations'])


if __name__ == '__main__':
    unittest.main()
