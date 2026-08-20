/** Every tile on the Markets board is the same height.
 *
 * Theirs is a uniform 440px across a three-column grid, and that regularity is
 * most of why their board reads as composed rather than assembled — tiles that
 * size themselves to their content produce a ragged bottom edge on every row.
 *
 * The widget header costs 38px, so the body takes the remainder.
 */
export const TILE_HEIGHT = 440
export const TILE_BODY_HEIGHT = TILE_HEIGHT - 38
