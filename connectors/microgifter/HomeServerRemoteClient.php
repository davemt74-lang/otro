<?php
declare(strict_types=1);

/**
 * Drop-in server-side HomeServer Remote Bridge client for Microgifter.
 *
 * Token persistence is intentionally left to the host application so this
 * connector never writes credentials to disk, session, logs, or source files.
 */
final class MicrogifterHomeServerRemoteClient
{
    public const DEFAULT_PERMISSIONS = [
        'agent.chat',
        'awareness.read',
        'contacts.read',
        'events.read',
        'events.write',
        'knowledge.search',
        'memory.read',
        'memory.write',
        'notifications.read',
        'plugins.read',
        'tasks.read',
        'tasks.write',
        'tools.execute',
        'usage.read',
        'usage.write',
    ];

    private const PUBLIC_OPERATIONS = ['capabilities', 'pair.request', 'pair.status'];
    private const RELAY_PATHS = ['/health', '/v1/claim', '/v1/session', '/v1/session/rotate', '/v1/request'];

    private string $relayBaseUrl;
    private ?string $relayToken;
    private ?string $homeServerToken;
    private int $timeoutSeconds;

    public function __construct(
        string $relayBaseUrl,
        ?string $relayToken = null,
        ?string $homeServerToken = null,
        int $timeoutSeconds = 10
    ) {
        $this->relayBaseUrl = self::validateRelayBaseUrl($relayBaseUrl);
        $this->relayToken = self::cleanToken($relayToken);
        $this->homeServerToken = self::cleanToken($homeServerToken);
        $this->timeoutSeconds = max(3, min(30, $timeoutSeconds));
    }

    private static function validateRelayBaseUrl(string $value): string
    {
        $value = rtrim(trim($value), '/');
        $parts = parse_url($value);
        if (!is_array($parts) || empty($parts['scheme']) || empty($parts['host'])) {
            throw new InvalidArgumentException('A valid HomeServer relay URL is required.');
        }
        $scheme = strtolower((string)$parts['scheme']);
        $host = strtolower((string)$parts['host']);
        $loopback = in_array($host, ['localhost', '127.0.0.1', '::1'], true);
        if ($scheme !== 'https' && !($scheme === 'http' && $loopback)) {
            throw new InvalidArgumentException('HomeServer relay must use HTTPS except on loopback.');
        }
        if (isset($parts['user'], $parts['pass']) || isset($parts['query']) || isset($parts['fragment'])) {
            throw new InvalidArgumentException('HomeServer relay URL cannot contain credentials, query, or fragment data.');
        }
        return $value;
    }

    private static function cleanToken(?string $token): ?string
    {
        $token = trim((string)$token);
        return $token === '' ? null : $token;
    }

    public function setRelayToken(?string $token): self
    {
        $this->relayToken = self::cleanToken($token);
        return $this;
    }

    public function setHomeServerToken(?string $token): self
    {
        $this->homeServerToken = self::cleanToken($token);
        return $this;
    }

    public function clearTokens(): void
    {
        $this->relayToken = null;
        $this->homeServerToken = null;
    }

    public function health(): array
    {
        return $this->relay('GET', '/health', null, false);
    }

    public function claim(string $claimCode): array
    {
        $claimCode = strtoupper(trim($claimCode));
        if (!preg_match('/^[A-Z0-9-]{8,40}$/', $claimCode)) {
            throw new InvalidArgumentException('Enter the HomeServer Remote Bridge claim code.');
        }
        $result = $this->relay('POST', '/v1/claim', ['claim_code'=>$claimCode], false);
        $relayToken = self::cleanToken((string)($result['relay_token'] ?? ''));
        if ($relayToken === null || empty($result['device_id'])) {
            throw new RuntimeException('HomeServer relay returned an invalid claim response.');
        }
        $this->relayToken = $relayToken;
        return $result;
    }

    public function rotateRelaySession(): array
    {
        $result = $this->relay('POST', '/v1/session/rotate', []);
        $token = self::cleanToken((string)($result['relay_token'] ?? ''));
        if ($token === null) throw new RuntimeException('Relay did not return a replacement session token.');
        $this->relayToken = $token;
        return $result;
    }

    public function requestPairing(array $permissions = self::DEFAULT_PERMISSIONS): array
    {
        $pairing = $this->request('pair.request', [
            'app_key'=>'microgifter',
            'app_name'=>'Microgifter',
            'permissions'=>array_values(array_unique(array_map('strval', $permissions))),
        ]);
        if (empty($pairing['request_id']) || empty($pairing['claim_token']) || empty($pairing['code'])) {
            throw new RuntimeException('HomeServer returned an unsupported pairing response.');
        }
        return $pairing;
    }

    public function pairingStatus(array $pairing): array
    {
        $requestId = trim((string)($pairing['request_id'] ?? ''));
        $claimToken = trim((string)($pairing['claim_token'] ?? ''));
        if ($requestId === '' || $claimToken === '') {
            throw new InvalidArgumentException('Pairing request_id and claim_token are required.');
        }
        $status = $this->request('pair.status', [
            'request_id'=>$requestId,
            'claim_token'=>$claimToken,
        ]);
        if (!empty($status['ready'])) $this->homeServerToken = $claimToken;
        return $status;
    }

    public function request(string $operation, array $payload = []): array
    {
        $operation = trim($operation);
        if ($operation === '') throw new InvalidArgumentException('HomeServer operation is required.');
        $public = in_array($operation, self::PUBLIC_OPERATIONS, true);
        if (!$public && $this->homeServerToken === null) {
            throw new RuntimeException('Microgifter is not paired for protected HomeServer capabilities.');
        }
        $result = $this->relay('POST', '/v1/request', [
            'operation'=>$operation,
            'payload'=>$payload,
            'bearer_token'=>$public ? null : $this->homeServerToken,
        ]);
        $payloadResult = $result['payload'] ?? [];
        return is_array($payloadResult) ? $payloadResult : [];
    }

    public function capabilities(): array { return $this->request('capabilities'); }
    public function inferenceStatus(): array { return $this->request('inference.status'); }
    public function chat(string $message, ?string $conversationId = null): array
    {
        return $this->request('agent.chat', ['message'=>$message, 'conversation_id'=>$conversationId]);
    }
    public function contacts(string $query = ''): array { return $this->request('contacts.search', ['query'=>$query]); }
    public function searchKnowledge(string $query = ''): array { return $this->request('knowledge.search', ['query'=>$query]); }
    public function memory(): array { return $this->request('memory.read'); }
    public function tools(): array { return $this->request('tools.list'); }
    public function skills(): array { return $this->request('skills.list'); }
    public function plugins(): array { return $this->request('plugins.list'); }
    public function appScope(): array
    {
        $tools = $this->tools();
        return is_array($tools['app_scope'] ?? null) ? $tools['app_scope'] : [];
    }
    public function executeTool(string $toolKey, array $arguments = []): array
    {
        $toolKey = trim($toolKey);
        if ($toolKey === '') throw new InvalidArgumentException('toolKey is required.');
        return $this->request('tool.execute', ['tool_key'=>$toolKey, 'arguments'=>$arguments]);
    }

    private function relay(string $method, string $path, ?array $payload = null, bool $requiresRelayToken = true): array
    {
        if (!in_array($path, self::RELAY_PATHS, true)) throw new RuntimeException('Unsupported HomeServer relay path.');
        if ($requiresRelayToken && $this->relayToken === null) throw new RuntimeException('HomeServer relay has not been claimed.');
        if (!function_exists('curl_init')) throw new RuntimeException('cURL is required for HomeServer Remote Bridge access.');

        $headers = ['Accept: application/json'];
        if ($requiresRelayToken) $headers[] = 'Authorization: Bearer '.$this->relayToken;
        $method = strtoupper($method);
        $ch = curl_init($this->relayBaseUrl.$path);
        if ($ch === false) throw new RuntimeException('Could not initialize HomeServer relay request.');
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER=>true,
            CURLOPT_HEADER=>false,
            CURLOPT_FOLLOWLOCATION=>false,
            CURLOPT_CONNECTTIMEOUT=>3,
            CURLOPT_TIMEOUT=>$this->timeoutSeconds,
            CURLOPT_HTTPHEADER=>$headers,
            CURLOPT_PROTOCOLS=>CURLPROTO_HTTP | CURLPROTO_HTTPS,
        ]);
        if ($method !== 'GET') {
            $json = json_encode($payload ?? [], JSON_UNESCAPED_SLASHES);
            if (!is_string($json)) { curl_close($ch); throw new RuntimeException('Could not encode HomeServer request.'); }
            $headers[] = 'Content-Type: application/json';
            curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
            curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
            curl_setopt($ch, CURLOPT_POSTFIELDS, $json);
        }
        $body = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        curl_close($ch);
        if (!is_string($body)) throw new RuntimeException('HomeServer relay connection failed.');
        $data = json_decode($body, true);
        if (!is_array($data)) throw new RuntimeException('HomeServer relay returned an invalid response.');
        if ($status < 200 || $status >= 300) {
            $detail = trim((string)($data['detail'] ?? $data['payload']['detail'] ?? ''));
            throw new RuntimeException($detail !== '' ? $detail : 'HomeServer relay request failed.');
        }
        return $data;
    }
}
