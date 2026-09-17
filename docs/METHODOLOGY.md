# Notas metodológicas de coleta

## 1. Busca não é censo do YouTube

`search.list` é um mecanismo de descoberta, não uma enumeração exaustiva de todo o universo de vídeos que poderiam ser semanticamente relacionados a um tema. O corpus resultante depende das queries, variantes, janela temporal, ordenação e profundidade de paginação. Por isso `search_hits` é parte do dado metodológico e não deve ser descartado.

## 2. Separar descoberta de unidade analítica

Um mesmo `video_id` pode ser recuperado por várias queries. Isso é preservado em `search_hits`, mas a unidade é deduplicada em `videos`. Essa separação permite medir cobertura e sobreposição entre estratégias de busca sem duplicar a análise do conteúdo.

## 3. Momento da captura

Métricas como views, likes, comentários e inscritos são snapshots. Todas as bases recebem `captured_at`/campos equivalentes. Comparações temporais devem considerar o momento da coleta e mudanças de definição da própria plataforma.

Em particular, a documentação oficial do YouTube informa mudança na contagem de `viewCount` a partir de 24/08/2026 para vídeos longos, Lives e Shorts, passando a contar a visualização quando o vídeo começa a tocar, incluindo autoplay e outras formas de início. Para séries históricas que cruzem essa data, trate comparabilidade de views explicitamente.

## 4. Transcrição

A YouTube Data API não é usada como fonte genérica de download de captions de vídeos públicos de terceiros. A camada de transcrição é independente e registra `transcript_method`, `transcript_status`, idioma e erro. `youtube-transcript-api` e `yt-dlp` dependem de interfaces do YouTube que podem mudar; o fallback Whisper é local e opcional.

## 5. Comentários

A unidade primária é `comment_id`. Comentários de primeiro nível e replies permanecem no mesmo dataset, diferenciados por `is_reply`, `thread_id` e `parent_comment_id`. O pipeline usa `comments.list` para recuperar replies quando habilitado, porque o subconjunto eventualmente embutido em `commentThreads` não garante todas as respostas.

## 6. Reprodutibilidade mínima

Para cada estudo, preserve:

- arquivo YAML usado na coleta;
- `search_hits`;
- JSON de auditoria de cada execução;
- timestamps de captura;
- versão do repositório/commit;
- registro de alterações posteriores nas queries.

Esses itens permitem reconstruir o desenho de amostragem mesmo que métricas e conteúdo público mudem posteriormente.


## Política de transcrição v0.2

A recuperação de fala segue uma cascata: legenda preferida via `youtube-transcript-api` → legenda via `yt-dlp` → transcrição do áudio via Faster Whisper. Falhas não são tratadas como conclusão definitiva quando `retry_failed: true`; apenas `transcript_status=success` entra no conjunto de itens concluídos para retomada. O método efetivamente utilizado é preservado em `transcript_method`.
