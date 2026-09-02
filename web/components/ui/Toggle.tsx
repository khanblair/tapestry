"use client";

export interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label?: string;
}

/**
 * The switch control (.switch / .switch.on in app/globals.css), ported
 * from the prototype's toggle-row switches (Settings/Platforms/Tools
 * panels). A real, accessible checkbox under the hood rather than a bare
 * clickable div, unlike the prototype's non-interactive mock.
 */
export function Toggle({ checked, onChange, disabled = false, label }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={`switch${checked ? " on" : ""}`}
      onClick={() => onChange(!checked)}
    />
  );
}
