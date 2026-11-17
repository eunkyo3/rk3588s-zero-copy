#!/usr/bin/env python3
"""M4's post-processing rules for mAP (prereg/M4.md §2.3), in Python: the implementation that runs on the
FP32 outputs, and on the board's INT8 outputs for the check of §8 step 3 against the C++ one.

Both rules take a model's outputs as float32 arrays in the order the model gives them, 3 branches of
NCHW tensors with N = 1: YOLOv8n's (box, class, score sum) and YOLOv10n's (box, class). The box tensor has
4 × 16 DFL bins per cell, the class tensor 80 scores after their sigmoid. INT8 outputs are first made
float32 by dequantize(), the C++ expression (q − zp) × scale.

Arithmetic follows the C++ of src/core/yolov10_post.cpp and yolov8_post.cpp, in float32, in the same order:
  - a candidate is a (cell, class) pair whose score is above conf (0.001);
  - candidates are ordered by score, highest first; equal scores by branch (stride 8, 16, 32), then cell
    (row-major), then class;
  - a box is decoded from its own cell: per side, the softmax's expectation over the DFL bins, summed bin
    by bin; then x1 = (x + 0.5 − d_left) × stride, y1, x2, y2 likewise, in model input coordinates.
The rules:
  - yolov10_rule: the max_det (300) best candidates. No NMS.
  - yolov8_rule: of the first 30,000 candidates, going down the order, a candidate is dropped when its
    IoU with an earlier kept candidate of the same class is above 0.7 (IoU as yolov8_post computes it,
    on boxes in model input coordinates, no class offset); the first 300 kept are the result.
Each returns a Result: the detections, how many candidates there were, and whether the rule's next
detection (the 301st) has the 300th's score, i.e. whether the 300th score was tied (§6).
"""
from typing import NamedTuple

import numpy as np

CONF = np.float32(0.001)  # §2.3: Ultralytics' val default
MAX_DET = 300
MAX_NMS = 30000  # Ultralytics' max_nms
NMS_IOU = np.float32(0.7)
CLASSES = 80
MODEL_W = MODEL_H = 640
HALF = np.float32(0.5)


class Result(NamedTuple):
    cls: np.ndarray  # (n,) int
    score: np.ndarray  # (n,) float32
    box: np.ndarray  # (n, 4) float32: x1, y1, x2, y2 in model input coordinates
    candidates: int  # (cell, class) pairs above conf
    tied: bool  # the rule's (max_det + 1)-th detection has the max_det-th's score


def dequantize(q, zp, scale):
    """INT8 outputs as the C++ dequantize: (float(q) − zp) × scale, in float32."""
    return (np.asarray(q).astype(np.float32) - np.float32(zp)) * np.float32(scale)


def branches(outputs, per_branch):
    """[(box, cls)] of the 3 branches of a model's outputs, with per_branch tensors each (2 or 3), checked."""
    if len(outputs) != 3 * per_branch:
        raise ValueError(f"expected {3 * per_branch} outputs, got {len(outputs)}")
    out = []
    for b in range(3):
        box, cls = outputs[b * per_branch], outputs[b * per_branch + 1]
        for t in outputs[b * per_branch:(b + 1) * per_branch]:
            if t.dtype != np.float32 or t.ndim != 4 or t.shape[0] != 1 or t.shape[2:] != box.shape[2:]:
                raise ValueError(f"branch {b}: expected float32 1×C×H×W tensors on one grid")
        if box.shape[1] < 4 or box.shape[1] % 4 or cls.shape[1] != CLASSES:
            raise ValueError(f"branch {b}: unexpected channels {box.shape[1]} and {cls.shape[1]}")
        if per_branch == 3 and outputs[b * 3 + 2].shape[1] != 1:
            raise ValueError(f"branch {b}: the score sum must have 1 channel")
        out.append((box, cls))
    return out


def decode_cells(box, model_w=MODEL_W, model_h=MODEL_H):
    """(H·W, 4) float32 boxes of every cell of a branch, as the C++ decode_dfl and box expression."""
    _, channels, grid_h, grid_w = box.shape
    dfl = channels // 4
    bins = box[0].reshape(4, dfl, grid_h * grid_w).transpose(2, 0, 1)  # (cells, side, bin)
    e = np.exp(bins - bins.max(axis=2, keepdims=True))
    total = np.zeros(bins.shape[:2], np.float32)
    acc = np.zeros(bins.shape[:2], np.float32)
    for i in range(dfl):  # bin by bin, as the C++ loop sums
        total = total + e[:, :, i]
        acc = acc + e[:, :, i] * np.float32(i)
    d = acc / total
    stride_x = np.float32(model_w) / np.float32(grid_w)
    stride_y = np.float32(model_h) / np.float32(grid_h)
    cell = np.arange(grid_h * grid_w)
    x, y = (cell % grid_w).astype(np.float32), (cell // grid_w).astype(np.float32)
    return np.stack([(x + HALF - d[:, 0]) * stride_x, (y + HALF - d[:, 1]) * stride_y,
                     (x + HALF + d[:, 2]) * stride_x, (y + HALF + d[:, 3]) * stride_y], axis=1)


def ordered_candidates(br, conf, limit):
    """The first `limit` candidates in the rule's order, as (score, branch, cell, class) arrays, and how many
    there were in all."""
    scores, branch, cells, classes = [], [], [], []
    for b, (_, cls) in enumerate(br):
        per_cell = cls[0].reshape(CLASSES, -1).T  # (cells, classes): row-major is (cell, class) order
        cell, c = np.nonzero(per_cell > conf)
        scores.append(per_cell[cell, c])
        branch.append(np.full(cell.size, b))
        cells.append(cell)
        classes.append(c)
    score = np.concatenate(scores)
    order = np.argsort(-score, kind="stable")[:limit]  # stable: equal scores keep (branch, cell, class)
    return (score[order], np.concatenate(branch)[order], np.concatenate(cells)[order],
            np.concatenate(classes)[order]), score.size


def boxes_of(br, branch, cell):
    """(n, 4) boxes of the given (branch, cell) pairs, each decoded from its own cell."""
    out = np.zeros((branch.size, 4), np.float32)
    for b, (box, _) in enumerate(br):
        sel = branch == b
        if sel.any():
            out[sel] = decode_cells(box)[cell[sel]]
    return out


def iou_with(kept, b):
    """IoU of box b with each kept box, as yolov8_post's iou(kept, b), in float32."""
    w = np.maximum(np.float32(0), np.minimum(kept[:, 2], b[2]) - np.maximum(kept[:, 0], b[0]))
    h = np.maximum(np.float32(0), np.minimum(kept[:, 3], b[3]) - np.maximum(kept[:, 1], b[1]))
    inter = w * h
    union = (kept[:, 2] - kept[:, 0]) * (kept[:, 3] - kept[:, 1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return np.where(union > 0, inter / np.where(union > 0, union, np.float32(1)), np.float32(0))


def yolov10_rule(outputs, conf=CONF, max_det=MAX_DET):
    """§2.3's YOLOv10n rule: the max_det best (cell, class) candidates above conf, without NMS."""
    br = branches(outputs, 2)
    (score, branch, cell, cls), total = ordered_candidates(br, conf, max_det + 1)
    tied = score.size > max_det and score[max_det] == score[max_det - 1]
    score, branch, cell, cls = score[:max_det], branch[:max_det], cell[:max_det], cls[:max_det]
    return Result(cls, score, boxes_of(br, branch, cell), total, bool(tied))


def yolov8_rule(outputs, conf=CONF, max_det=MAX_DET, max_nms=MAX_NMS, iou_thresh=NMS_IOU):
    """§2.3's YOLOv8n rule: multi-label candidates above conf, the first max_nms of them, greedy NMS per class
    at iou_thresh going down their order, and the first max_det kept. The score sums are not used."""
    br = branches(outputs, 3)
    (score, branch, cell, cls), total = ordered_candidates(br, conf, max_nms)
    box = boxes_of(br, branch, cell)
    kept, per_class = [], {}
    for k in range(score.size):
        prior = per_class.get(cls[k])
        if prior and (iou_with(box[prior], box[k]) > iou_thresh).any():
            continue
        kept.append(k)
        per_class.setdefault(cls[k], []).append(k)
        if len(kept) > max_det:  # the (max_det + 1)-th, only to tell a tie
            break
    tied = len(kept) > max_det and score[kept[max_det]] == score[kept[max_det - 1]]
    kept = np.array(kept[:max_det], dtype=np.int64)
    return Result(cls[kept], score[kept], box[kept], total, bool(tied))


RULES = {"yolov8n": yolov8_rule, "yolov10n": yolov10_rule}


def g9(v):
    """A float32 value as the board tool prints it: %.9g of its exact value."""
    return format(float(np.float32(v)), ".9g")
