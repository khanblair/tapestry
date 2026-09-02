"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "danger" | "ghost" | "default";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "default" | "sm";
  block?: boolean;
  children: ReactNode;
}

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  default: "",
  primary: "btn-primary",
  danger: "btn-danger",
  ghost: "btn-ghost",
};

/**
 * Matches the prototype's .btn / .btn-primary / .btn-danger / .btn-ghost
 * classes exactly (see app/globals.css). Plain <button> underneath so it
 * works as a form submit control, a click handler, or (via `asChild`-free
 * composition) inside a Next <Link> wrapper when a variant needs to be a
 * link — wrap the Link around this component's rendered className.
 */
export function Button({
  variant = "default",
  size = "default",
  block = false,
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = ["btn", VARIANT_CLASS[variant], size === "sm" ? "btn-sm" : "", block ? "btn-block" : "", className]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={classes} {...rest}>
      {children}
    </button>
  );
}
