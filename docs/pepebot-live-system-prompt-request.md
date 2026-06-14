# Feature request: system prompt (`systemInstruction`) support in the Live API

## Summary
The Live API (`/v1/live`) currently has **no way to set a system prompt** for the
session. The upstream Gemini/Vertex Live model therefore runs with only the
default behavior plus tool definitions — there is no place to give it a role,
persona, or task instructions. Please add `systemInstruction` support, ideally
both as config (default) and as a per-session override in the client `setup`
message.

## Current behavior (why it's missing)
- `pkg/live/vertex_live.go` `SetupMessage()` and `pkg/live/gemini_live.go`
  `SetupMessage()` build the `BidiGenerateContentSetup` with only `model`,
  `generationConfig`, and `realtimeInputConfig`. **No `systemInstruction`.**
- `pkg/live/live.go` (~L272–282): the gateway takes `provider.SetupMessage(model)`
  and then **only** injects tools (`injectGeminiToolsIntoSetup`). The client's
  `setup` message (provider/model/agent/enable_tools) is **not** forwarded to the
  upstream as-is, so a client cannot supply `systemInstruction` either.
- `pkg/config/config.go` (`LiveConfig`, ~L146): has `generation_config` and
  `realtime_input_config` but **no** system-prompt field.
- The agent system-prompt machinery (`pkg/agent/context.go BuildSystemPrompt()`,
  `pkg/agent/registry.go PromptFile / AgentPromptDir`) is used by the **text**
  agent only — the Live session never calls it. Skills (`SKILL.md`) are likewise
  loaded into the text agent's context, **not** the Live session, so skill
  guidance never reaches the Live model.

Net effect: Live sessions can call tools but cannot be instructed *how* to behave
(e.g. "you are an autonomous rover: perceive → act → repeat, avoid obstacles,
stop on command"). Any autonomous behavior today is just model initiative from
tool descriptions.

## Requested feature
Allow a `systemInstruction` to be set on the Live session, from (in order of
precedence):
1. **Per-session override** in the client `setup` message — `setup.system_prompt`
   (string). Most flexible: clients (e.g. a rover/voice client) can set
   task-specific instructions per session without redeploying the gateway.
2. **Config default** — `live.system_prompt` (string) and/or
   `live.system_prompt_file` (path), with env `PEPEBOT_LIVE_SYSTEM_PROMPT`.
3. **(Nice to have) Agent prompt** — reuse the selected `agent`'s prompt file
   (`AgentRegistry` / `ContextBuilder`) as the Live `systemInstruction`, so an
   agent's persona is consistent between text and Live. Optionally also append
   triggered skills' bodies.

Resolution order suggestion: client `setup.system_prompt` > `live.system_prompt`
(/_file) > agent prompt file. If none set, behave exactly as today.

## Suggested implementation
Both Gemini and Vertex Live accept a top-level `systemInstruction` in the setup:

```json
{ "setup": { "systemInstruction": { "parts": [ { "text": "<PROMPT>" } ] }, "model": "...", "generationConfig": { } } }
```

1. `pkg/config/config.go` — add to `LiveConfig`:
   ```go
   SystemPrompt     string `json:"system_prompt,omitempty" env:"PEPEBOT_LIVE_SYSTEM_PROMPT"`
   SystemPromptFile string `json:"system_prompt_file,omitempty" env:"PEPEBOT_LIVE_SYSTEM_PROMPT_FILE"`
   ```
2. `pkg/live/vertex_live.go` + `pkg/live/gemini_live.go` `SetupMessage()` —
   after building `setupInner`, set the instruction when present:
   ```go
   if prompt := p.liveConfig.SystemPrompt; prompt != "" {
       setupInner["systemInstruction"] = map[string]interface{}{
           "parts": []map[string]interface{}{{"text": prompt}},
       }
   }
   ```
   (load `SystemPromptFile` into `SystemPrompt` at config load if set.)
3. `pkg/live/live.go` — accept `system_prompt` from the parsed client setup, and
   after `setupData := provider.SetupMessage(model)` add an
   `injectGeminiSystemInstruction(setupData, prompt)` (mirroring
   `injectGeminiToolsIntoSetup`) so a per-session override wins. Optionally source
   the prompt from the `agent`'s `PromptFile` when no explicit prompt is given.

## Config example
```json
{
  "live": {
    "enabled": true,
    "provider": "vertex",
    "model": "gemini-live-2.5-flash-native-audio",
    "video": true,
    "system_prompt": "You are LEXA, an autonomous rover. You can see the camera and call rover tools. Given a goal, work toward it in a perceive→act→perceive loop with small bounded steps; avoid obstacles (back off + turn on blocked); stop immediately when asked. Narrate briefly."
  }
}
```

## Client setup example (per-session override)
```json
{ "setup": { "provider": "vertex", "model": "gemini-live-2.5-flash-native-audio",
             "agent": "default", "enable_tools": true,
             "system_prompt": "You are LEXA, an autonomous rover ..." } }
```

## Acceptance criteria
- Setting `live.system_prompt` (or `setup.system_prompt`) causes the upstream
  `BidiGenerateContentSetup` to include `systemInstruction.parts[0].text`.
- The Live model demonstrably follows the instruction (e.g. role/behavior).
- When unset, the upstream setup is byte-identical to today (no regression).
- Works for both `vertex` and `gemini` providers.
