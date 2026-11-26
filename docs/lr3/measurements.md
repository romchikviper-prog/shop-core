| Режим | Клієнт | Успіх, % | Деградовано, % | p50, мс | p95, мс | p99, мс | макс, мс | Викликів | Спроби | CB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| normal | naive | 100.0 | 0.0 | 106.8 | 124.5 | 161.8 | 163.2 | 200 | 200 | disabled |
| normal | protected | 100.0 | 0.0 | 107.7 | 125.7 | 188.0 | 190.5 | 200 | 200 | closed |
| slow | naive | 100.0 | 0.0 | 1020.2 | 1042.6 | 1043.9 | 1046.4 | 200 | 200 | disabled |
| slow | protected | 0.0 | 100.0 | 2.9 | 29.7 | 387.3 | 527.1 | 8 | 8 | open |
| errors | naive | 48.5 | 51.5 | 42.3 | 112.5 | 124.1 | 125.9 | 200 | 200 | disabled |
| errors | protected | 93.0 | 7.0 | 119.7 | 642.5 | 748.9 | 762.6 | 362 | 362 | closed |
| unavailable | naive | 0.0 | 100.0 | 4.7 | 28.2 | 40.8 | 539.8 | 200 | 200 | disabled |
| unavailable | protected | 0.0 | 100.0 | 2.8 | 29.2 | 101.3 | 510.3 | 8 | 8 | open |

### Переходи Circuit Breaker

- **1. Після серії збоїв (8 невдалих спроб)**: `open`; closed->open
- **2. Після паузи: Half-open і невдалий пробний виклик**: `open`; closed->open → open->half_open → half_open->open
- **3. Після паузи й успішного пробного виклику**: `closed`; closed->open → open->half_open → half_open->open → open->half_open → half_open->closed
