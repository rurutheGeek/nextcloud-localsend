<?php
/**
 * @var \OCP\IL10N $l
 * @var array $_ the template parameters from Settings\Admin
 */
?>
<div id="localsend_share" class="section">
	<h2><?php p($l->t('LocalSend Share')); ?></h2>
	<p class="settings-hint">
		<?php p($l->t('URL of the LocalSend relay reachable from this Nextcloud server, e.g. http://192.168.1.10:53200. The relay must run on the same LAN as the receiving devices; its setup is documented in the server/ directory of the app repository.')); ?>
	</p>

	<?php if ($_['status'] === 'ok'): ?>
		<p class="success"><?php p($l->t('Saved. The relay answered the health check.')); ?></p>
	<?php elseif ($_['status'] === 'unreachable'): ?>
		<p class="warning"><?php p($l->t('Saved, but the relay did not answer the health check. Check the URL, the token and the network.')); ?></p>
	<?php elseif ($_['status'] === 'incomplete'): ?>
		<p class="warning"><?php p($l->t('Saved. Set both the relay URL and the token to enable sending.')); ?></p>
	<?php endif; ?>

	<form method="post" action="<?php p($_['save_url']); ?>">
		<input type="hidden" name="requesttoken" value="<?php p($_['requesttoken']); ?>">
		<p>
			<label for="localsend_share_relay_url"><?php p($l->t('Relay URL')); ?></label><br>
			<input type="url" id="localsend_share_relay_url" name="relayUrl" class="text"
				style="width: 24em" value="<?php p($_['relay_url']); ?>"
				placeholder="http://192.168.1.10:53200">
		</p>
		<p>
			<label for="localsend_share_relay_token"><?php p($l->t('Relay token')); ?></label><br>
			<input type="password" id="localsend_share_relay_token" name="relayToken" class="text"
				style="width: 24em" value="" autocomplete="new-password"
				placeholder="<?php p($_['token_set'] ? $l->t('stored — leave empty to keep') : $l->t('shared secret')); ?>">
		</p>
		<?php if ($_['token_set']): ?>
			<p>
				<label>
					<input type="checkbox" name="clearToken" value="1">
					<?php p($l->t('Remove the stored token')); ?>
				</label>
			</p>
		<?php endif; ?>
		<button type="submit" class="button primary"><?php p($l->t('Save')); ?></button>
	</form>
	<h3><?php p($l->t('Receiving')); ?></h3>
	<p class="settings-hint">
		<?php p($l->t('Optionally, the relay can also act as a LocalSend receiver. Senders must enter the PIN, and accepted files are stored in the folder of the user below. The relay needs LOCALSEND_RECEIVE_NEXTCLOUD_URL pointing at this Nextcloud.')); ?>
	</p>
	<p>
		<strong>
			<?php if (!$_['receive_open']): ?>
				<?php p($l->t('Receiving is off.')); ?>
			<?php elseif ($_['receive_until'] !== ''): ?>
				<?php p($l->t('Receiving is on until %s.', [$_['receive_until']])); ?>
			<?php else: ?>
				<?php p($l->t('Receiving is on until it is turned off.')); ?>
			<?php endif; ?>
		</strong>
	</p>
	<form method="post" action="<?php p($_['receive_save_url']); ?>">
		<input type="hidden" name="requesttoken" value="<?php p($_['requesttoken']); ?>">
		<p>
			<label for="localsend_share_duration"><?php p($l->t('Accept files')); ?></label><br>
			<select id="localsend_share_duration" name="duration">
				<?php if ($_['receive_open']): ?>
					<option value="keep" selected><?php p($l->t('Keep as it is')); ?></option>
				<?php endif; ?>
				<option value="off"><?php p($l->t('Off')); ?></option>
				<option value="15m"><?php p($l->t('For 15 minutes')); ?></option>
				<option value="1h"><?php p($l->t('For 1 hour')); ?></option>
				<option value="1d"><?php p($l->t('For 1 day')); ?></option>
				<option value="always"><?php p($l->t('Until turned off')); ?></option>
			</select>
		</p>
		<p>
			<label for="localsend_share_pin"><?php p($l->t('PIN')); ?></label><br>
			<input type="password" id="localsend_share_pin" name="pin" class="text"
				style="width: 24em" value="" autocomplete="new-password" minlength="4"
				placeholder="<?php p($_['pin_set'] ? $l->t('stored — leave empty to keep') : $l->t('at least 4 characters')); ?>">
		</p>
		<?php if ($_['pin_set']): ?>
			<p>
				<label>
					<input type="checkbox" name="clearPin" value="1">
					<?php p($l->t('Remove the stored PIN (turns receiving off)')); ?>
				</label>
			</p>
		<?php endif; ?>
		<p>
			<label for="localsend_share_user"><?php p($l->t('Store files for user (user ID)')); ?></label><br>
			<input type="text" id="localsend_share_user" name="user" class="text"
				style="width: 24em" value="<?php p($_['receive']['user']); ?>">
		</p>
		<p>
			<label for="localsend_share_folder"><?php p($l->t('Folder in that user\'s files')); ?></label><br>
			<input type="text" id="localsend_share_folder" name="folder" class="text"
				style="width: 24em" value="<?php p($_['receive']['folder']); ?>">
		</p>
		<p>
			<label for="localsend_share_max"><?php p($l->t('Maximum file size (MiB)')); ?></label><br>
			<input type="number" id="localsend_share_max" name="maxMegabytes" min="1"
				value="<?php p((string)intdiv($_['receive']['maxBytes'], 1048576)); ?>">
		</p>
		<p>
			<label for="localsend_share_concurrent"><?php p($l->t('Transfers at the same time')); ?></label><br>
			<input type="number" id="localsend_share_concurrent" name="maxConcurrent" min="1"
				value="<?php p((string)$_['receive']['maxConcurrent']); ?>">
		</p>
		<p>
			<label>
				<input type="checkbox" name="keepLog" value="1" <?php if ($_['receive']['log']): ?>checked<?php endif; ?>>
				<?php p($l->t('Keep a log of received files')); ?>
			</label>
		</p>
		<?php if ($_['receive_log'] !== []): ?>
			<p>
				<label>
					<input type="checkbox" name="clearLog" value="1">
					<?php p($l->t('Clear the log')); ?>
				</label>
			</p>
		<?php endif; ?>
		<button type="submit" class="button primary"><?php p($l->t('Save')); ?></button>
	</form>
	<?php if ($_['receive_log'] !== []): ?>
		<h3><?php p($l->t('Recently received')); ?></h3>
		<ul>
			<?php foreach ($_['receive_log'] as $entry): ?>
				<li>
					<?php p($l->l('datetime', (int)($entry['time'] ?? 0))); ?> —
					<?php p((string)($entry['path'] ?? '')); ?>
					(<?php p(\OCP\Util::humanFileSize((int)($entry['size'] ?? 0))); ?>,
					<?php p($l->t('from %s', [(string)($entry['sender'] ?? '')])); ?>)
				</li>
			<?php endforeach; ?>
		</ul>
	<?php endif; ?>
</div>
