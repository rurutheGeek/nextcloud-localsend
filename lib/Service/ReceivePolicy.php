<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Service;

/**
 * Rules for receiving files, kept free of Nextcloud classes so they can be
 * tested with plain PHP.
 */
class ReceivePolicy {
	public const MIN_PIN_LENGTH = 4;
	public const DEFAULT_MAX_BYTES = 2147483648;
	public const DEFAULT_MAX_CONCURRENT = 1;
	public const DEFAULT_FOLDER = '/LocalSend';
	public const LOG_LIMIT = 50;

	/** Durations offered in the admin form, in seconds (0 = until turned off). */
	public const DURATIONS = ['off' => -1, '15m' => 900, '1h' => 3600, '1d' => 86400, 'always' => 0];

	/**
	 * Whether receiving is on right now.
	 *
	 * @param array{enabled: bool, until: int, pin: string, user: string} $settings
	 */
	public static function isOpen(array $settings, int $now): bool {
		if (!$settings['enabled'] || $settings['user'] === '') {
			return false;
		}
		if (!self::pinIsValid($settings['pin'])) {
			return false;
		}
		return $settings['until'] === 0 || $settings['until'] > $now;
	}

	public static function pinIsValid(string $pin): bool {
		return mb_strlen($pin) >= self::MIN_PIN_LENGTH && trim($pin) === $pin;
	}

	/** The "until" timestamp for a duration key, or null to turn receiving off. */
	public static function untilFor(string $duration, int $now): ?int {
		$seconds = self::DURATIONS[$duration] ?? -1;
		if ($seconds < 0) {
			return null;
		}
		return $seconds === 0 ? 0 : $now + $seconds;
	}

	/**
	 * A file name that is safe to create in one folder. LocalSend sends
	 * folder contents as "dir/file"; the path is flattened, not followed.
	 */
	public static function fileName(string $name): string {
		$name = str_replace(['\\', '/'], '_', $name);
		$name = preg_replace('/[\x00-\x1F\x7F]/u', '', $name) ?? '';
		$name = trim($name);
		$name = ltrim($name, '.');
		if (strlen($name) > 200) {
			$extension = pathinfo($name, PATHINFO_EXTENSION);
			$keep = $extension !== '' && strlen($extension) < 20 ? '.' . $extension : '';
			$name = mb_strcut($name, 0, 200 - strlen($keep)) . $keep;
		}
		if ($name === '' || $name === '.part' || str_ends_with($name, '.part')) {
			$name = ($name === '' ? 'received' : $name) . '.bin';
		}
		return $name;
	}

	/** A folder path inside the user's files, always starting with "/". */
	public static function folder(string $path): ?string {
		$parts = [];
		foreach (explode('/', str_replace('\\', '/', $path)) as $part) {
			$part = trim($part);
			if ($part === '' || $part === '.') {
				continue;
			}
			if ($part === '..') {
				return null;
			}
			$parts[] = $part;
		}
		return '/' . implode('/', $parts);
	}

	public static function positiveInt(string $value, int $default): int {
		$value = trim($value);
		if ($value === '' || !ctype_digit($value) || (int)$value < 1) {
			return $default;
		}
		return (int)$value;
	}

	/**
	 * Add an entry to the receive log, newest first, keeping LOG_LIMIT entries.
	 *
	 * @param list<array<string, mixed>> $log
	 * @param array<string, mixed> $entry
	 * @return list<array<string, mixed>>
	 */
	public static function appendLog(array $log, array $entry): array {
		array_unshift($log, $entry);
		return array_slice($log, 0, self::LOG_LIMIT);
	}
}
