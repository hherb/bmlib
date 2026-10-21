// bmlib — shared library for biomedical literature tools
// Copyright (C) 2024-2026 Dr Horst Herb
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

//! Crate-private port of Python's `difflib.SequenceMatcher`.
//!
//! Covers only `SequenceMatcher(None, a, b, autojunk=False)` over `char`
//! slices: no junk predicate and no popular-element heuristic, so CPython's
//! junk and `popular` branches in `find_longest_match` (the `isbjunk` tests
//! and the "extend by junk" loops) can never fire and are dropped. Results,
//! including float ratios, are bit-identical to CPython's. Ties break on the
//! earliest `a` index, then the earliest `b` index.

use std::collections::HashMap;

/// A run of `size` equal elements at `a[a..]` and `b[b..]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Block {
    /// Start in sequence `a`.
    pub(crate) a: usize,
    /// Start in sequence `b`.
    pub(crate) b: usize,
    /// Length of the run.
    pub(crate) size: usize,
}

/// Matcher with a fixed `b` (the passage) and a replaceable `a` (a text window).
pub(crate) struct SequenceMatcher {
    b: Vec<char>,
    /// For each element of `b`, its indices in ascending order.
    b2j: HashMap<char, Vec<usize>>,
    a: Vec<char>,
}

/// CPython's `_calculate_ratio`: `1.0` when both sequences are empty.
fn calculate_ratio(matches: usize, length: usize) -> f64 {
    if length > 0 {
        2.0 * matches as f64 / length as f64
    } else {
        1.0
    }
}

impl SequenceMatcher {
    /// Creates a matcher over `b` with an empty `a`.
    pub(crate) fn new(b: &[char]) -> Self {
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        for (j, &c) in b.iter().enumerate() {
            b2j.entry(c).or_default().push(j);
        }
        Self {
            b: b.to_vec(),
            b2j,
            a: Vec::new(),
        }
    }

    /// Replaces sequence `a`.
    pub(crate) fn set_a(&mut self, a: &[char]) {
        self.a = a.to_vec();
    }

    /// Longest match in `a[alo..ahi]` x `b[blo..bhi]`.
    fn find_longest_match(&self, alo: usize, ahi: usize, blo: usize, bhi: usize) -> Block {
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
        // j2len[j]: length of the longest match ending at a[i-1] and b[j].
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for i in alo..ahi {
            let mut newj2len = HashMap::new();
            if let Some(js) = self.b2j.get(&self.a[i]) {
                for &j in js {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let prev = if j > 0 {
                        j2len.get(&(j - 1)).copied().unwrap_or(0)
                    } else {
                        0
                    };
                    let k = prev + 1;
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = newj2len;
        }
        Block {
            a: besti,
            b: bestj,
            size: bestsize,
        }
    }

    /// Non-adjacent matching blocks in order, ending with `(len_a, len_b, 0)`.
    pub(crate) fn matching_blocks(&self) -> Vec<Block> {
        let (la, lb) = (self.a.len(), self.b.len());
        let mut queue = vec![(0, la, 0, lb)];
        let mut found = Vec::new();
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let x = self.find_longest_match(alo, ahi, blo, bhi);
            let Block {
                a: i,
                b: j,
                size: k,
            } = x;
            if k > 0 {
                found.push(x);
                if alo < i && blo < j {
                    queue.push((alo, i, blo, j));
                }
                if i + k < ahi && j + k < bhi {
                    queue.push((i + k, ahi, j + k, bhi));
                }
            }
        }
        found.sort();
        // Collapse adjacent blocks.
        let mut out = Vec::new();
        let mut cur = Block {
            a: 0,
            b: 0,
            size: 0,
        };
        for blk in found {
            if cur.a + cur.size == blk.a && cur.b + cur.size == blk.b {
                cur.size += blk.size;
            } else {
                if cur.size > 0 {
                    out.push(cur);
                }
                cur = blk;
            }
        }
        if cur.size > 0 {
            out.push(cur);
        }
        out.push(Block {
            a: la,
            b: lb,
            size: 0,
        });
        out
    }

    /// Similarity in `[0, 1]`: `2 * matches / (len_a + len_b)`.
    pub(crate) fn ratio(&self) -> f64 {
        let matches: usize = self.matching_blocks().iter().map(|b| b.size).sum();
        calculate_ratio(matches, self.a.len() + self.b.len())
    }

    /// Upper bound on [`ratio`](Self::ratio) from the multiset intersection.
    pub(crate) fn quick_ratio(&self) -> f64 {
        let mut avail: HashMap<char, usize> = HashMap::new();
        for &c in &self.b {
            *avail.entry(c).or_insert(0) += 1;
        }
        let mut matches = 0;
        for c in &self.a {
            if let Some(n) = avail.get_mut(c) {
                if *n > 0 {
                    *n -= 1;
                    matches += 1;
                }
            }
        }
        calculate_ratio(matches, self.a.len() + self.b.len())
    }

    /// Cheapest upper bound on [`ratio`](Self::ratio): the shorter length.
    pub(crate) fn real_quick_ratio(&self) -> f64 {
        let (la, lb) = (self.a.len(), self.b.len());
        calculate_ratio(la.min(lb), la + lb)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn check(a: &str, b: &str, blocks: &[(usize, usize, usize)], r: f64, q: f64, rq: f64) {
        let b: Vec<char> = b.chars().collect();
        let a: Vec<char> = a.chars().collect();
        let mut m = SequenceMatcher::new(&b);
        m.set_a(&a);
        let got: Vec<_> = m
            .matching_blocks()
            .iter()
            .map(|x| (x.a, x.b, x.size))
            .collect();
        assert_eq!(got, blocks);
        assert_eq!(m.ratio(), r);
        assert_eq!(m.quick_ratio(), q);
        assert_eq!(m.real_quick_ratio(), rq);
    }

    #[test]
    fn blocks_and_ratios_match_cpython() {
        check(
            "abxcd",
            "abcd",
            &[(0, 0, 2), (3, 2, 2), (5, 4, 0)],
            0.8888888888888888,
            0.8888888888888888,
            0.8888888888888888,
        );
        check(
            "abab",
            "ab",
            &[(0, 0, 2), (4, 2, 0)],
            0.6666666666666666,
            0.6666666666666666,
            0.6666666666666666,
        );
        check(
            "hazard ratio was 0.91",
            "the hazard ratio was 0.19",
            &[(0, 4, 19), (19, 24, 1), (21, 25, 0)],
            0.8695652173913043,
            0.9130434782608695,
            0.9130434782608695,
        );
        check("", "", &[(0, 0, 0)], 1.0, 1.0, 1.0);
        check("abc", "", &[(3, 0, 0)], 0.0, 0.0, 0.0);
    }
}
