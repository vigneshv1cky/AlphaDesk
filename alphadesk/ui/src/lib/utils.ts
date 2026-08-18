/** Anything a conditional class expression can evaluate to. Non-strings
 * (false from `cond && "x"`, 0 from a numeric guard) are dropped rather than
 * stringified — `0` and `true` are never intended as class names. */
export type ClassValue = string | number | boolean | null | undefined

/** Join truthy class fragments. Deliberately NOT clsx+tailwind-merge: those
 * were here only to serve shadcn's variant machinery, which this app no
 * longer has. Without twMerge, later classes do not automatically beat
 * earlier ones — so components here emit a minimal base and append the
 * caller's `className` last, and simply don't ship defaults for properties a
 * caller is expected to override. */
export function cn(...parts: ClassValue[]): string {
  return parts.filter((p): p is string => typeof p === "string" && p.length > 0).join(" ")
}
