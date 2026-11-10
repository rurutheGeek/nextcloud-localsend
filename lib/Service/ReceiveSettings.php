<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Service;

use OCP\IConfig;

/** Reads the receive settings stored by the admin form. */
class ReceiveSettings {
	private const APP = 'localsend_share';

	public function __construct(
		private IConfig $config,
	) {
	}

	/**
	 * @return array{enabled: bool, until: int, pin: string, user: string, folder: string,
	 *               maxBytes: int, maxConcurrent: int, log: bool}
	 */
	public function get(): array {
		return [
			'enabled' => $this->value('receive_enabled') === 'yes',
			'until' => (int)$this->value('receive_until', '0'),
			'pin' => $this->value('receive_pin'),
			'user' => $this->value('receive_user'),
			'folder' => ReceivePolicy::folder($this->value('receive_folder', ReceivePolicy::DEFAULT_FOLDER))
				?? ReceivePolicy::DEFAULT_FOLDER,
			'maxBytes' => ReceivePolicy::positiveInt(
				$this->value('receive_max_bytes'), ReceivePolicy::DEFAULT_MAX_BYTES),
			'maxConcurrent' => ReceivePolicy::positiveInt(
				$this->value('receive_max_concurrent'), ReceivePolicy::DEFAULT_MAX_CONCURRENT),
			'log' => $this->value('receive_log', 'yes') === 'yes',
		];
	}

	/** @return list<array<string, mixed>> */
	public function log(): array {
		$log = json_decode($this->value('receive_log_entries', '[]'), true);
		return is_array($log) ? array_values($log) : [];
	}

	/** @param array<string, mixed> $entry */
	public function record(array $entry): void {
		$this->config->setAppValue(self::APP, 'receive_log_entries',
			json_encode(ReceivePolicy::appendLog($this->log(), $entry), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
	}

	public function clearLog(): void {
		$this->config->deleteAppValue(self::APP, 'receive_log_entries');
	}

	private function value(string $key, string $default = ''): string {
		return $this->config->getAppValue(self::APP, $key, $default);
	}
}
