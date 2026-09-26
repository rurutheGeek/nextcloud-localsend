<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Controller;

use OCA\LocalSendShare\Settings\Admin;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\AuthorizedAdminSetting;
use OCP\AppFramework\Http\Attribute\FrontpageRoute;
use OCP\AppFramework\Http\RedirectResponse;
use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\IRequest;
use OCP\IURLGenerator;

class SettingsController extends Controller {
	public function __construct(
		string $appName,
		IRequest $request,
		private IConfig $config,
		private IClientService $clientService,
		private IURLGenerator $url,
	) {
		parent::__construct($appName, $request);
	}

	#[AuthorizedAdminSetting(settings: Admin::class)]
	#[FrontpageRoute(verb: 'POST', url: '/settings')]
	public function save(string $relayUrl = '', string $relayToken = '', bool $clearToken = false): RedirectResponse {
		$url = rtrim(trim($relayUrl), '/');
		$this->config->setAppValue('localsend_share', 'relay_url', $url);
		if ($clearToken) {
			$this->config->deleteAppValue('localsend_share', 'relay_token');
		} elseif (trim($relayToken) !== '') {
			$this->config->setAppValue('localsend_share', 'relay_token', trim($relayToken));
		}

		$status = $this->healthCheck($url);
		return new RedirectResponse(
			$this->url->linkToRoute('settings.AdminSettings.index', ['section' => 'localsend_share'])
			. '?localsend_share=' . urlencode($status)
		);
	}

	private function healthCheck(string $url): string {
		$token = $this->config->getAppValue('localsend_share', 'relay_token', '');
		if ($url === '' || $token === '') {
			return 'incomplete';
		}
		try {
			$response = $this->clientService->newClient()->get($url . '/healthz', [
				'headers' => ['Authorization' => 'Bearer ' . $token],
				'timeout' => 10,
				'nextcloud' => ['allow_local_address' => true],
			]);
			return $response->getStatusCode() === 200 ? 'ok' : 'unreachable';
		} catch (\Throwable) {
			return 'unreachable';
		}
	}
}
