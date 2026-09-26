<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Controller;

use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\FrontpageRoute;
use OCP\AppFramework\Http\Attribute\NoAdminRequired;
use OCP\AppFramework\Http\DataResponse;
use OCP\Files\File;
use OCP\Files\IRootFolder;
use OCP\Http\Client\IClientService;
use OCP\IConfig;
use OCP\IL10N;
use OCP\IRequest;
use OCP\IUserSession;
use Psr\Log\LoggerInterface;

class SendController extends Controller {
	public function __construct(
		string $appName,
		IRequest $request,
		private IRootFolder $rootFolder,
		private IClientService $clientService,
		private IConfig $config,
		private IUserSession $userSession,
		private IL10N $l,
		private LoggerInterface $logger,
		private ?string $userId,
	) {
		parent::__construct($appName, $request);
	}

	#[NoAdminRequired]
	#[FrontpageRoute(verb: 'GET', url: '/devices')]
	public function devices(): DataResponse {
		if (!$this->configured()) {
			return new DataResponse(
				['error' => $this->l->t('The LocalSend relay is not configured yet')],
				Http::STATUS_SERVICE_UNAVAILABLE
			);
		}
		try {
			$response = $this->client()->get($this->baseUrl() . '/devices', [
				'headers' => $this->authHeaders(),
				'timeout' => 30,
				'http_errors' => false,
				'nextcloud' => ['allow_local_address' => true],
			]);
			$payload = json_decode((string)$response->getBody(), true);
		} catch (\Throwable $error) {
			$this->logger->error('localsend_share: device lookup failed', ['exception' => $error]);
			return new DataResponse(
				['error' => $this->l->t('Could not load the device list (the relay is unreachable)')],
				Http::STATUS_BAD_GATEWAY
			);
		}
		if ($response->getStatusCode() !== 200) {
			return new DataResponse(
				['error' => $this->relayError($payload, 'Could not load the device list')],
				Http::STATUS_BAD_GATEWAY
			);
		}
		if (!is_array($payload) || !isset($payload['devices'])) {
			return new DataResponse(
				['error' => $this->l->t('Could not read the device list')],
				Http::STATUS_BAD_GATEWAY
			);
		}
		return new DataResponse(['devices' => $payload['devices']]);
	}

	#[NoAdminRequired]
	#[FrontpageRoute(verb: 'POST', url: '/send')]
	public function send(int $fileId, string $fingerprint): DataResponse {
		if ($this->userId === null) {
			return new DataResponse(['error' => $this->l->t('Authentication required')], Http::STATUS_UNAUTHORIZED);
		}
		if (!$this->configured()) {
			return new DataResponse(
				['error' => $this->l->t('The LocalSend relay is not configured yet')],
				Http::STATUS_SERVICE_UNAVAILABLE
			);
		}
		$node = $this->findFile($fileId);
		if ($node === null) {
			return new DataResponse(['error' => $this->l->t('File not found')], Http::STATUS_NOT_FOUND);
		}
		if (!$node->isReadable()) {
			return new DataResponse(['error' => $this->l->t('You are not allowed to read this file')], Http::STATUS_FORBIDDEN);
		}
		$handle = $node->fopen('r');
		if ($handle === false) {
			return new DataResponse(['error' => $this->l->t('Could not open the file')], Http::STATUS_INTERNAL_SERVER_ERROR);
		}
		$displayName = $this->userSession->getUser()?->getDisplayName() ?? '';

		try {
			$response = $this->client()->post($this->baseUrl() . '/send', [
				'headers' => $this->authHeaders() + [
					'Content-Type' => 'application/octet-stream',
					'Content-Length' => (string)$node->getSize(),
					'X-Send-To' => $fingerprint,
					'X-Send-Filename' => rawurlencode($node->getName()),
					'X-Send-User' => rawurlencode($displayName),
				],
				'body' => $handle,
				'timeout' => 300,
				'http_errors' => false,
				'nextcloud' => ['allow_local_address' => true],
			]);
		} catch (\Throwable $error) {
			$this->logger->error('localsend_share: send failed', ['exception' => $error]);
			return new DataResponse(
				['error' => $this->l->t('Sending failed (open LocalSend on the device and try again)')],
				Http::STATUS_BAD_GATEWAY
			);
		} finally {
			if (is_resource($handle)) {
				fclose($handle);
			}
		}

		$result = json_decode((string)$response->getBody(), true);
		if ($response->getStatusCode() !== 200) {
			return new DataResponse(
				['error' => $this->relayError($result, 'Could not confirm the transfer')],
				Http::STATUS_BAD_GATEWAY
			);
		}
		if (!is_array($result) || !isset($result['status'])) {
			return new DataResponse(
				['error' => $this->l->t('Could not confirm the transfer')],
				Http::STATUS_BAD_GATEWAY
			);
		}
		return new DataResponse($result);
	}

	private function relayError(mixed $payload, string $fallback): string {
		if (is_array($payload) && isset($payload['error']) && is_string($payload['error'])) {
			return $payload['error'];
		}
		return $this->l->t($fallback);
	}

	private function configured(): bool {
		return $this->config->getAppValue('localsend_share', 'relay_url', '') !== ''
			&& $this->config->getAppValue('localsend_share', 'relay_token', '') !== '';
	}

	private function baseUrl(): string {
		return rtrim($this->config->getAppValue('localsend_share', 'relay_url', ''), '/');
	}

	private function authHeaders(): array {
		return ['Authorization' => 'Bearer '
			. $this->config->getAppValue('localsend_share', 'relay_token', '')];
	}

	private function client() {
		return $this->clientService->newClient();
	}

	private function findFile(int $fileId): ?File {
		$folder = $this->rootFolder->getUserFolder($this->userId);
		foreach ($folder->getById($fileId) as $node) {
			if ($node instanceof File) {
				return $node;
			}
		}
		return null;
	}
}
