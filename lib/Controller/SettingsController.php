<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Controller;

use OCA\LocalSendShare\Service\ReceivePolicy;
use OCA\LocalSendShare\Service\ReceiveSettings;
use OCA\LocalSendShare\Settings\Admin;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http\Attribute\AuthorizedAdminSetting;
use OCP\AppFramework\Http\Attribute\FrontpageRoute;
use OCP\AppFramework\Http\RedirectResponse;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\IRequest;
use OCP\IURLGenerator;
use OCP\IUserManager;

class SettingsController extends Controller {
	public function __construct(
		string $appName,
		IRequest $request,
		private IConfig $config,
		private IClientService $clientService,
		private IURLGenerator $url,
		private IUserManager $userManager,
		private ReceiveSettings $receiveSettings,
		private ITimeFactory $time,
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

		return $this->back($this->healthCheck($url));
	}

	#[AuthorizedAdminSetting(settings: Admin::class)]
	#[FrontpageRoute(verb: 'POST', url: '/settings/receive')]
	public function saveReceive(
		string $duration = 'off',
		string $pin = '',
		bool $clearPin = false,
		string $user = '',
		string $folder = '',
		string $maxMegabytes = '',
		string $maxConcurrent = '',
		bool $keepLog = false,
		bool $clearLog = false,
	): RedirectResponse {
		$app = 'localsend_share';
		if ($clearPin) {
			$this->config->deleteAppValue($app, 'receive_pin');
		} elseif ($pin !== '') {
			if (!ReceivePolicy::pinIsValid($pin)) {
				return $this->back('receive_bad_pin');
			}
			$this->config->setAppValue($app, 'receive_pin', $pin);
		}
		$user = trim($user);
		$this->config->setAppValue($app, 'receive_user', $user);
		$path = ReceivePolicy::folder($folder === '' ? ReceivePolicy::DEFAULT_FOLDER : $folder);
		if ($path === null || $path === '/') {
			return $this->back('receive_bad_folder');
		}
		$this->config->setAppValue($app, 'receive_folder', $path);
		$megabytes = ReceivePolicy::positiveInt($maxMegabytes, intdiv(ReceivePolicy::DEFAULT_MAX_BYTES, 1048576));
		$this->config->setAppValue($app, 'receive_max_bytes', (string)($megabytes * 1048576));
		$this->config->setAppValue($app, 'receive_max_concurrent',
			(string)ReceivePolicy::positiveInt($maxConcurrent, ReceivePolicy::DEFAULT_MAX_CONCURRENT));
		$this->config->setAppValue($app, 'receive_log', $keepLog ? 'yes' : 'no');
		if ($clearLog) {
			$this->receiveSettings->clearLog();
		}

		$current = $this->receiveSettings->get();
		$until = $duration === 'keep'
			? (ReceivePolicy::isOpen($current, $this->time->getTime()) ? $current['until'] : null)
			: ReceivePolicy::untilFor($duration, $this->time->getTime());
		if ($until === null) {
			$this->config->setAppValue($app, 'receive_enabled', 'no');
			return $this->back('receive_saved');
		}
		// Never open without a PIN and an existing target user.
		$storedPin = $this->config->getAppValue($app, 'receive_pin', '');
		if (!ReceivePolicy::pinIsValid($storedPin) || $user === '' || !$this->userManager->userExists($user)) {
			$this->config->setAppValue($app, 'receive_enabled', 'no');
			return $this->back('receive_incomplete');
		}
		$this->config->setAppValue($app, 'receive_until', (string)$until);
		$this->config->setAppValue($app, 'receive_enabled', 'yes');
		return $this->back('receive_on');
	}

	private function back(string $status): RedirectResponse {
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
