import AMD
import collections
from scipy.spatial.distance import squareform, pdist
import numpy as np
from itertools import permutations, combinations


def _collapse_into_groups(overlapping):
    overlapping = squareform(overlapping)
    group_nums = {}  # row_ind: group number
    group = 0
    for i, row in enumerate(overlapping):
        if i not in group_nums:
            group_nums[i] = group
            group += 1

            for j in np.argwhere(row).T[0]:
                if j not in group_nums:
                    group_nums[j] = group_nums[i]

    groups = collections.defaultdict(list)
    for row_ind, group_num in sorted(group_nums.items()):
        groups[group_num].append(row_ind)
    groups = list(groups.values())

    return groups


def custom_PDD(
        periodic_set,
        k: int,
        lexsort: bool = False,
        collapse: bool = True,
        collapse_tol: float = 1e-4,
        return_row_groups: bool = True,
        constrained: bool = True,
        return_angles: bool = False,
) -> [np.ndarray]:
    motif, cell, asymmetric_unit, weights = extract_motif_cell(periodic_set)
    weights = np.full((len(motif),), 1 / len(motif))
    dists, cloud, inds = AMD.nearest_neighbours(motif, cell, motif, k)
    groups = [[i] for i in range(len(dists))]
    if return_angles:
        angles = get_angles(motif, cloud, inds)

    if collapse and collapse_tol >= 0:
        overlapping = pdist(dists, metric='chebyshev')
        overlapping = overlapping <= collapse_tol
        types_match = pdist(periodic_set.types.reshape((-1, 1))) == 0
        neighbors_match = (pdist(periodic_set.types[inds % periodic_set.types.shape[0]]) == 0)

        if constrained:
            overlapping = overlapping & types_match & neighbors_match

        if return_angles:
            angles_overlapping = pdist(dists, metric='chebyshev') <= collapse_tol
            overlapping = overlapping & angles_overlapping

        if overlapping.any():
            groups = _collapse_into_groups(overlapping)
            weights = np.array([sum(weights[group]) for group in groups])
            dists = np.array([np.average(dists[group], axis=0) for group in groups])
            if return_angles:
                angles = np.array([np.average(angles[group], axis=0) for group in groups])

    pdd = np.hstack((weights[:, None], dists))

    if lexsort:
        lex_ordering = np.lexsort(np.rot90(dists))
        if return_row_groups:
            groups = [groups[i] for i in lex_ordering]
        pdd = pdd[lex_ordering]
        if return_angles:
            angles = angles[lex_ordering]

    if return_row_groups:
        if return_angles:
            return pdd, groups, inds, cloud, angles
        return pdd, groups, inds, cloud
    else:
        if return_angles:
            return pdd, inds, cloud, angles
        return pdd, inds, cloud


def extract_motif_cell(pset: AMD.PeriodicSet):
    if isinstance(pset, AMD.PeriodicSet):
        motif, cell = pset.motif, pset.cell
        asym_unit = pset.asymmetric_unit
        wyc_muls = pset.wyckoff_multiplicities
        if asym_unit is None or wyc_muls is None:
            asymmetric_unit = motif
            weights = np.full((len(motif),), 1 / len(motif))
        else:
            asymmetric_unit = pset.motif[asym_unit]
            weights = wyc_muls / np.sum(wyc_muls)
    else:
        motif, cell = pset
        asymmetric_unit = motif
        weights = np.full((len(motif),), 1 / len(motif))

    return motif, cell, asymmetric_unit, weights


def unit_vector(vector, axis=-1):
    return vector / np.linalg.norm(vector, axis=axis)[:, :, :, None]


def get_angles(motif, cloud, inds):
    neighbor_vectors = cloud[inds] - motif[:, None, :]
    vc = list(combinations(range(inds.shape[1]), 2))
    angle_vectors = np.array([neighbor_vectors[i][vc] for i in range(motif.shape[0])])
    #  Shape is (number of motif points, number of neighbors, (two vectors), number of combos, 3D point)
    uv = unit_vector(angle_vectors)
    angles = np.arccos(np.clip(np.sum(uv[:, :, 0, :] * uv[:, :, 1, :], axis=-1), -1.0, 1.0))
    angles = angles.reshape((motif.shape[0], len(vc)))
    return angles

def phi(groups, inds, cloud, cell):
    inds_in_cell = [i[0] for i in groups]
    coords_in_cell = cloud[inds_in_cell]
    ri_rj = coords_in_cell[:, None, :] - cloud[inds[inds_in_cell]]
    term1 = np.pi * np.eye(3) * np.matmul(np.linalg.inv(cell), ri_rj[:, :, :, None])
    sum_term1 = np.matmul(cell, np.sum(np.eye(3) * np.sin(term1) ** 2, axis=-2)[:, :, :, None])
    return 1 / np.linalg.norm(np.squeeze(sum_term1), axis=-1)


def coulomb_matrix(ps, pdd, inds, groups):
    to_keep = [i[0] for i in groups]
    c = ps.types[to_keep, None] * ps.types[inds[to_keep] % len(groups)] / pdd[:, 1:]
    return np.hstack([pdd[:, 0:1], c])

