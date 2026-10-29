# Protocolo WebSocket (`/ws`)

Mensagens JSON trocadas entre a interface e o servidor.

## Cliente → servidor

| `type` | Campos | Efeito |
|---|---|---|
| `chat` | `text` | Envia uma frase ao JARVIS |
| `confirm` | `id`, `approve` | Aprova/recusa uma ação sensível pendente |
| `set_brain` | `brain` (`claude`, `openai`, `gemini`, `local`) | Troca o cérebro |
| `reset` | — | Apaga o histórico da conversa |

## Servidor → cliente

| `type` | Campos | Quando |
|---|---|---|
| `hello` | `brain`, `label` | Ao conectar e ao trocar de cérebro |
| `stats` | `data` (snapshot completo) | A cada 1,5 s |
| `state` | `value` (`thinking` / `idle`) | Início e fim do processamento |
| `reply` | `text`, `brain` | Resposta final (é falada pela interface) |
| `action` | `tool`, `args`, `status`, `mensagem` | Cada ferramenta executada |
| `confirm_request` | `id`, `tool`, `texto` | Ação sensível aguardando autorização |
| `confirm_resolved` | `id` | Ação aprovada/recusada |
| `warning` | `mensagem` | Erros não fatais (ex.: IA indisponível → modo offline) |

## Fluxo de uma ação sensível

```
usuário: "encerre o chrome"
  → cérebro chama encerrar_processo(alvo="chrome")
  → tools.py NÃO executa: guarda como pendente e emite confirm_request
  → cérebro responde "Encerrar 12 processos 'chrome.exe'? Aguardo sua confirmação."
usuário: clica CONFIRMAR (ou diz "confirmar")
  → servidor executa: terminate() → wait 3s → kill() nos que sobraram
  → registra em logs/acoes.jsonl e responde "12 processo(s) encerrado(s)."
```
