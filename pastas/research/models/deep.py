"""Global LSTM and 1D-CNN seed ensembles (after Wunsch et al. 2021, HESS 25, 1671-1687).

With about two years per series a network per series cannot be trained, so one network
learns all series at once. All series share one forcing station, so the weather history of
a day is the same input for every series: the encoder turns the last ``SEQ_DAYS`` days of
P, E and season into a vector once per *date*, and a small head combines it with a learned
embedding of the series. The target is the head standardised with the series' training
mean and spread.

Training: the calendar-cut blocks of ``evaluate.global_training``; the last 20 % of each
block is the early-stopping set, the weights of the best epoch are kept. The prediction is
the mean over ``seeds`` networks, as in Wunsch et al. No measured head is an input, so the
network simulates from the forcing alone.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.evaluate import Task, global_training, inner_split
from research.models import Context, days

SEQ_DAYS = 365
DATES_PER_BATCH = 64
MAX_EPOCHS = 200
PATIENCE = 20


def _net(kind: str, n_series: int, channels: int):
    import torch
    from torch import nn

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            # Both read the last 364 days as 52 weekly steps of 7 daily values each: the
            # daily detail stays, the sequence is 7x shorter (fast enough on a CPU).
            if kind == "lstm":
                self.encoder = nn.LSTM(channels * 7, 32, batch_first=True)
                width = 32
            else:
                layers, c_in = [], channels * 7
                for dilation in (1, 2, 4, 8, 10):  # receptive field 1 + 2 * 25 = 51 weeks
                    layers += [nn.Conv1d(c_in, 32, kernel_size=3, dilation=dilation), nn.ReLU()]
                    c_in = 32
                self.encoder = nn.Sequential(*layers)
                width = 64
            self.embedding = nn.Embedding(n_series, 8)
            self.head = nn.Sequential(nn.Linear(width + 8, 32), nn.ReLU(), nn.Dropout(0.1), nn.Linear(32, 1))

        def encode(self, sequences):  # (dates, SEQ_DAYS, channels) -> (dates, width)
            weeks = sequences[:, -364:, :].reshape(sequences.shape[0], 52, -1)
            if kind == "lstm":
                _, (hidden, _) = self.encoder(weeks)
                return hidden[-1]
            z = self.encoder(weeks.transpose(1, 2))
            return torch.cat([z[:, :, -1], z.mean(dim=2)], dim=1)

        def forward(self, sequences, date_index, series_index):
            z = self.encode(sequences)[date_index]
            return self.head(torch.cat([z, self.embedding(series_index)], dim=1)).squeeze(1)

    return Net()


class _Data:
    """Sequences per date, and (series, date, target) pairs to train on."""

    def __init__(self, ctx: Context, dates: pd.DatetimeIndex):
        values = ctx.sequence.to_numpy(dtype=np.float32)
        position = ctx.sequence.index.get_indexer(dates)
        if (position < SEQ_DAYS - 1).any():
            raise ValueError("forcing does not reach far enough back for the sequences")
        self.dates = dates
        self.windows = np.stack([values[p - SEQ_DAYS + 1:p + 1] for p in position])

    def pairs(self, heads: dict[str, pd.Series], codes: dict, stats: dict):
        series, date, target = [], [], []
        for sid, head in heads.items():
            mean, std = stats[sid]
            series.append(np.full(len(head), codes[sid]))
            date.append(self.dates.get_indexer(head.index))
            target.append((head.to_numpy() - mean) / std)
        return np.concatenate(series), np.concatenate(date), np.concatenate(target).astype(np.float32)


def _train_one(kind, seed, data, n_series, train_pairs, check_pairs):
    import torch

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    net = _net(kind, n_series, data.windows.shape[2])
    optimiser = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-5)
    windows = torch.from_numpy(data.windows)
    s_tr, d_tr, y_tr = (torch.from_numpy(a) for a in train_pairs)
    s_ck, d_ck, y_ck = (torch.from_numpy(a) for a in check_pairs)
    train_dates = np.unique(train_pairs[1])
    check_dates = np.unique(check_pairs[1])

    def loss_on(dates_used, s, d, y):
        # Encode only the dates in this batch, then look the pairs up by date.
        local = torch.full((len(data.dates),), -1, dtype=torch.long)
        local[torch.from_numpy(dates_used)] = torch.arange(len(dates_used))
        keep = local[d] >= 0
        prediction = net(windows[torch.from_numpy(dates_used)], local[d[keep]], s[keep])
        return torch.mean((prediction - y[keep]) ** 2)

    best, best_state, waited = np.inf, None, 0
    for _ in range(MAX_EPOCHS):
        net.train()
        for batch in np.array_split(rng.permutation(train_dates), max(1, len(train_dates) // DATES_PER_BATCH)):
            optimiser.zero_grad()
            loss_on(batch, s_tr, d_tr, y_tr).backward()
            optimiser.step()
        net.eval()
        with torch.no_grad():
            check = float(loss_on(check_dates, s_ck, d_ck, y_ck))
        if check < best - 1e-4:
            best, waited = check, 0
            best_state = {k: v.clone() for k, v in net.state_dict().items()}
        else:
            waited += 1
            if waited >= PATIENCE:
                break
    net.load_state_dict(best_state)
    net.eval()
    return net


def simulate_all(tasks: list[Task], ctx: Context, kind: str, seeds: int = 10) -> dict[str, pd.Series]:
    import torch

    torch.set_num_threads(4)  # 8 threads was 4x slower on this 8-core machine
    heads = {sid: h for sid, h in global_training(tasks).items() if len(h) >= 30}
    codes = {sid: i for i, sid in enumerate(sorted(heads))}
    stats = {sid: (float(h.mean()), float(h.std()) or 1.0) for sid, h in heads.items()}
    first = min(t.series.head.index[0] for t in tasks)
    last = max(t.series.head.index[-1] for t in tasks)
    data = _Data(ctx, pd.date_range(first, last, freq="D"))
    fit_heads, check_heads = {}, {}
    for sid, head in heads.items():
        fit_heads[sid], check_heads[sid] = inner_split(head)
    train_pairs = data.pairs(fit_heads, codes, stats)
    check_pairs = data.pairs(check_heads, codes, stats)

    windows = torch.from_numpy(data.windows)
    all_dates = torch.arange(len(data.dates))
    total = {sid: np.zeros(len(data.dates)) for sid in heads}
    for seed in range(seeds):
        net = _train_one(kind, seed, data, len(codes), train_pairs, check_pairs)
        with torch.no_grad():
            encoded = net.encode(windows)
            for sid in heads:
                series = torch.full((len(data.dates),), codes[sid], dtype=torch.long)
                z = torch.cat([encoded[all_dates], net.embedding(series)], dim=1)
                total[sid] += net.head(z).squeeze(1).numpy()
    out = {}
    for task in tasks:
        sid = task.meetreeks_id
        if sid not in heads:
            continue
        mean, std = stats[sid]
        simulated = pd.Series(total[sid] / seeds * std + mean, index=data.dates)
        out[sid] = simulated.reindex(days(task.series.head))
    return out
