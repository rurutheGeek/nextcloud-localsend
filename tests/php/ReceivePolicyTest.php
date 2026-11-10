<?php

declare(strict_types=1);

// Plain-PHP checks of the receive rules; run with `php tests/php/ReceivePolicyTest.php`.

require __DIR__ . '/../../lib/Service/ReceivePolicy.php';

use OCA\LocalSendShare\Service\ReceivePolicy;

$failures = 0;
function check(string $name, mixed $actual, mixed $expected): void {
	global $failures;
	if ($actual !== $expected) {
		$failures++;
		fwrite(STDERR, "FAIL $name: expected " . var_export($expected, true) . ', got ' . var_export($actual, true) . "\n");
	} else {
		echo "ok   $name\n";
	}
}

$now = 1_000_000;
$open = ['enabled' => true, 'until' => 0, 'pin' => '2468', 'user' => 'alice'];
check('enabled with a PIN and a user is open', ReceivePolicy::isOpen($open, $now), true);
check('receiving is off unless enabled', ReceivePolicy::isOpen(['enabled' => false] + $open, $now), false);
check('no PIN means closed', ReceivePolicy::isOpen(['pin' => ''] + $open, $now), false);
check('a too short PIN means closed', ReceivePolicy::isOpen(['pin' => '12'] + $open, $now), false);
check('no user means closed', ReceivePolicy::isOpen(['user' => ''] + $open, $now), false);
check('a timed window is open before its end', ReceivePolicy::isOpen(['until' => $now + 1] + $open, $now), true);
check('a timed window closes at its end', ReceivePolicy::isOpen(['until' => $now] + $open, $now), false);

check('off turns receiving off', ReceivePolicy::untilFor('off', $now), null);
check('unknown durations turn receiving off', ReceivePolicy::untilFor('forever!', $now), null);
check('15 minutes', ReceivePolicy::untilFor('15m', $now), $now + 900);
check('always has no end', ReceivePolicy::untilFor('always', $now), 0);

check('PIN with surrounding spaces is rejected', ReceivePolicy::pinIsValid(' 2468'), false);
check('four characters are enough', ReceivePolicy::pinIsValid('abcd'), true);

check('a plain name is kept', ReceivePolicy::fileName('photo.jpg'), 'photo.jpg');
check('folders are flattened', ReceivePolicy::fileName('dir/sub/a.txt'), 'dir_sub_a.txt');
check('path traversal is flattened', ReceivePolicy::fileName('../../etc/passwd'), '_.._etc_passwd');
check('leading dots are removed', ReceivePolicy::fileName('.htaccess'), 'htaccess');
check('control characters are removed', ReceivePolicy::fileName("a\nb\x00.txt"), 'ab.txt');
check('an empty name gets a fallback', ReceivePolicy::fileName('...'), 'received.bin');
check('part files are renamed', ReceivePolicy::fileName('x.part'), 'x.part.bin');
$long = ReceivePolicy::fileName(str_repeat('あ', 100) . '.jpg');
check('long names are cut and keep the extension', [strlen($long) <= 200, str_ends_with($long, '.jpg'), mb_check_encoding($long, 'UTF-8')], [true, true, true]);

check('folder paths are normalised', ReceivePolicy::folder('LocalSend//inbox/'), '/LocalSend/inbox');
check('".." in the folder is refused', ReceivePolicy::folder('/a/../b'), null);
check('the root folder normalises to "/"', ReceivePolicy::folder(''), '/');

check('a positive number is accepted', ReceivePolicy::positiveInt('3', 1), 3);
check('zero falls back', ReceivePolicy::positiveInt('0', 1), 1);
check('garbage falls back', ReceivePolicy::positiveInt('-5', 7), 7);

$log = [];
for ($i = 0; $i < ReceivePolicy::LOG_LIMIT + 5; $i++) {
	$log = ReceivePolicy::appendLog($log, ['n' => $i]);
}
check('the log keeps the newest entries first', [count($log), $log[0]['n']], [ReceivePolicy::LOG_LIMIT, ReceivePolicy::LOG_LIMIT + 4]);

exit($failures === 0 ? 0 : 1);
