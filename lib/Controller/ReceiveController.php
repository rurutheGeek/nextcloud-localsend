<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Controller;

use OCA\LocalSendShare\Service\ReceivePolicy;
use OCA\LocalSendShare\Service\ReceiveSettings;
use OCP\AppFramework\Controller;
use OCP\AppFramework\Http;
use OCP\AppFramework\Http\Attribute\BruteForceProtection;
use OCP\AppFramework\Http\Attribute\FrontpageRoute;
use OCP\AppFramework\Http\Attribute\NoCSRFRequired;
use OCP\AppFramework\Http\Attribute\PublicPage;
use OCP\AppFramework\Http\DataResponse;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\Files\Folder;
use OCP\Files\IRootFolder;
use OCP\IConfig;
use OCP\IRequest;
use OCP\IUserManager;
use Psr\Log\LoggerInterface;

/**
 * Endpoints for the relay's receiver. They are authenticated with the relay
 * token, not with a Nextcloud session: the relay is the only caller.
 */
class ReceiveController extends Controller {
	public function __construct(
		string $appName,
		IRequest $request,
		private IConfig $config,
		private ReceiveSettings $settings,
		private IRootFolder $rootFolder,
		private IUserManager $userManager,
		private ITimeFactory $time,
		private LoggerInterface $logger,
	) {
		parent::__construct($appName, $request);
	}

	#[PublicPage]
	#[NoCSRFRequired]
	#[BruteForceProtection(action: 'localsend_share_receive')]
	#[FrontpageRoute(verb: 'GET', url: '/receive/config')]
	public function config(): DataResponse {
		if (!$this->authorized()) {
			return $this->denied();
		}
		$settings = $this->settings->get();
		if (!$this->open($settings)) {
			return new DataResponse(['enabled' => false]);
		}
		return new DataResponse([
			'enabled' => true,
			'pin' => $settings['pin'],
			'maxBytes' => $settings['maxBytes'],
			'maxConcurrent' => $settings['maxConcurrent'],
		]);
	}

	#[PublicPage]
	#[NoCSRFRequired]
	#[BruteForceProtection(action: 'localsend_share_receive')]
	#[FrontpageRoute(verb: 'PUT', url: '/receive/file')]
	public function file(): DataResponse {
		if (!$this->authorized()) {
			return $this->denied();
		}
		$settings = $this->settings->get();
		if (!$this->open($settings)) {
			return new DataResponse(['error' => 'Receiving is turned off'], Http::STATUS_FORBIDDEN);
		}
		$size = (int)$this->request->getHeader('Content-Length');
		if ($size > $settings['maxBytes']) {
			return new DataResponse(['error' => 'File too large'], Http::STATUS_REQUEST_ENTITY_TOO_LARGE);
		}
		$name = ReceivePolicy::fileName(rawurldecode($this->request->getHeader('X-Receive-Filename')));
		$sender = mb_substr(rawurldecode($this->request->getHeader('X-Receive-Sender')), 0, 100);

		$input = fopen('php://input', 'rb');
		if ($input === false) {
			return new DataResponse(['error' => 'Could not read the upload'], Http::STATUS_BAD_REQUEST);
		}
		try {
			$folder = $this->targetFolder($settings['user'], $settings['folder']);
			$name = $folder->getNonExistingName($name);
			$file = $folder->newFile($name);
			$file->putContent($input);
			$stored = (int)$file->getSize();
			if ($stored !== $size) {
				$file->delete();
				return new DataResponse(['error' => 'The upload was incomplete'], Http::STATUS_BAD_REQUEST);
			}
		} catch (\Throwable $error) {
			$this->logger->error('localsend_share: storing a received file failed', ['exception' => $error]);
			return new DataResponse(['error' => 'Could not store the file'], Http::STATUS_INTERNAL_SERVER_ERROR);
		} finally {
			fclose($input);
		}

		$path = rtrim($settings['folder'], '/') . '/' . $name;
		$this->logger->info('localsend_share: received {name} from {sender}',
			['name' => $path, 'sender' => $sender, 'user' => $settings['user']]);
		if ($settings['log']) {
			$this->settings->record([
				'time' => $this->time->getTime(),
				'sender' => $sender,
				'path' => $path,
				'size' => $stored,
			]);
		}
		return new DataResponse(['status' => 'stored', 'path' => $path, 'bytes' => $stored]);
	}

	/** @param array{enabled: bool, until: int, pin: string, user: string} $settings */
	private function open(array $settings): bool {
		return ReceivePolicy::isOpen($settings, $this->time->getTime())
			&& $this->userManager->userExists($settings['user']);
	}

	private function targetFolder(string $user, string $path): Folder {
		$folder = $this->rootFolder->getUserFolder($user);
		foreach (array_filter(explode('/', $path), fn ($part) => $part !== '') as $part) {
			$folder = $folder->nodeExists($part) ? $folder->get($part) : $folder->newFolder($part);
			if (!$folder instanceof Folder) {
				throw new \RuntimeException('The receive folder path contains a file');
			}
		}
		return $folder;
	}

	private function authorized(): bool {
		$token = $this->config->getAppValue('localsend_share', 'relay_token', '');
		$header = $this->request->getHeader('Authorization');
		return $token !== '' && hash_equals('Bearer ' . $token, $header);
	}

	private function denied(): DataResponse {
		$response = new DataResponse(['error' => 'unauthorized'], Http::STATUS_UNAUTHORIZED);
		$response->throttle(['action' => 'localsend_share_receive']);
		return $response;
	}
}
