<?php

declare(strict_types=1);

namespace OCA\LocalSendShare\Settings;

use OCA\LocalSendShare\Service\ReceivePolicy;
use OCA\LocalSendShare\Service\ReceiveSettings;
use OCP\AppFramework\Http\TemplateResponse;
use OCP\AppFramework\Utility\ITimeFactory;
use OCP\IConfig;
use OCP\IL10N;
use OCP\IRequest;
use OCP\IURLGenerator;
use OCP\Settings\IDelegatedSettings;

class Admin implements IDelegatedSettings {
	public function __construct(
		private IConfig $config,
		private IRequest $request,
		private IURLGenerator $url,
		private IL10N $l,
		private ReceiveSettings $receiveSettings,
		private ITimeFactory $time,
	) {
	}

	public function getForm(): TemplateResponse {
		$receive = $this->receiveSettings->get();
		$open = ReceivePolicy::isOpen($receive, $this->time->getTime());
		return new TemplateResponse('localsend_share', 'admin', [
			'receive' => $receive,
			'receive_open' => $open,
			'receive_until' => $open && $receive['until'] > 0
				? $this->l->l('datetime', $receive['until']) : '',
			'receive_log' => $this->receiveSettings->log(),
			'receive_save_url' => $this->url->linkToRoute('localsend_share.settings.saveReceive'),
			'pin_set' => $receive['pin'] !== '',
			'relay_url' => $this->config->getAppValue('localsend_share', 'relay_url', ''),
			'token_set' => $this->config->getAppValue('localsend_share', 'relay_token', '') !== '',
			'status' => (string)$this->request->getParam('localsend_share', ''),
			'save_url' => $this->url->linkToRoute('localsend_share.settings.save'),
		]);
	}

	public function getSection(): string {
		return 'localsend_share';
	}

	public function getPriority(): int {
		return 50;
	}

	public function getName(): ?string {
		return $this->l->t('LocalSend Share');
	}

	public function getAuthorizedAppConfig(): array {
		return ['localsend_share' => ['/relay_url/', '/relay_token/', '/receive_.*/']];
	}
}
