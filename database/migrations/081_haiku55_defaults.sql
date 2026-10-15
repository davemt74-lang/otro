-- One-time main-model upgrade. Later owner choices are never rewritten.
UPDATE model_providers
SET model='claude-haiku-5-5', updated_at=CURRENT_TIMESTAMP
WHERE provider_key='anthropic';

UPDATE inference_settings
SET preferred_provider='anthropic', updated_at=CURRENT_TIMESTAMP
WHERE id=1;
