/**
 * Hand-authored line icons, ported from the prototype's `icon(name, size)`
 * JS helper into individual React components. Not explicitly named in the
 * original file list, but every screen in the prototype (mine and both
 * siblings') is built on this exact icon set — keeping it here in
 * components/ui/ (the design-system home) avoids three independent,
 * slightly-different icon sets appearing across the app.
 */
import type { SVGProps } from "react";

export interface IconProps extends SVGProps<SVGSVGElement> {
  size?: number;
}

function makeIcon(path: string) {
  function IconComponent({ size = 18, ...props }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        dangerouslySetInnerHTML={{ __html: path }}
        {...props}
      />
    );
  }
  return IconComponent;
}

export const SearchIcon = makeIcon('<circle cx="10.5" cy="10.5" r="6.5"/><line x1="20" y1="20" x2="15.3" y2="15.3"/>');
export const SettingsIcon = makeIcon('<circle cx="12" cy="12" r="3.2"/><path d="M19.2 12a7.2 7.2 0 0 0-.13-1.36l2.06-1.6-2-3.46-2.43.98a7.3 7.3 0 0 0-2.36-1.36L14 2.5h-4l-.34 2.7a7.3 7.3 0 0 0-2.36 1.36l-2.43-.98-2 3.46 2.06 1.6a7.2 7.2 0 0 0 0 2.72l-2.06 1.6 2 3.46 2.43-.98a7.3 7.3 0 0 0 2.36 1.36L10 21.5h4l.34-2.7a7.3 7.3 0 0 0 2.36-1.36l2.43.98 2-3.46-2.06-1.6c.09-.45.13-.9.13-1.36Z"/>');
export const BellIcon = makeIcon('<path d="M6 9a6 6 0 0 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 13 6 9Z"/><path d="M9.5 18a2.5 2.5 0 0 0 5 0"/>');
export const PlusIcon = makeIcon('<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>');
export const BackIcon = makeIcon('<polyline points="15 5 8 12 15 19"/>');
export const XIcon = makeIcon('<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>');
export const SunIcon = makeIcon('<circle cx="12" cy="12" r="4.2"/><line x1="12" y1="2.5" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="21.5"/><line x1="2.5" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="21.5" y2="12"/><line x1="5" y1="5" x2="6.7" y2="6.7"/><line x1="17.3" y1="17.3" x2="19" y2="19"/><line x1="19" y1="5" x2="17.3" y2="6.7"/><line x1="6.7" y1="17.3" x2="5" y2="19"/>');
export const MoonIcon = makeIcon('<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5Z"/>');
export const CheckIcon = makeIcon('<polyline points="4 12.5 9.5 18 20 6"/>');
export const SendIcon = makeIcon('<line x1="21" y1="3" x2="10" y2="14"/><polygon points="21 3 14.5 21 10 14 3 9.5 21 3"/>');
export const UsersIcon = makeIcon('<circle cx="8.5" cy="8" r="3.2"/><path d="M2.5 20c0-3.6 2.7-6 6-6s6 2.4 6 6"/><circle cx="17" cy="9" r="2.6"/><path d="M15.2 14.2c2.6.3 4.3 2.5 4.3 5.8"/>');
export const UserIcon = makeIcon('<circle cx="12" cy="8" r="3.6"/><path d="M4.5 20c0-4.2 3.3-6.8 7.5-6.8s7.5 2.6 7.5 6.8"/>');
export const MonitorIcon = makeIcon('<rect x="3" y="4.5" width="18" height="12" rx="1.6"/><line x1="8" y1="20" x2="16" y2="20"/><line x1="12" y1="16.5" x2="12" y2="20"/>');
export const TabletIcon = makeIcon('<rect x="6" y="2.5" width="12" height="19" rx="2"/><line x1="12" y1="18.4" x2="12" y2="18.5"/>');
export const PhoneIcon = makeIcon('<rect x="7.5" y="2.5" width="9" height="19" rx="2"/><line x1="12" y1="18" x2="12" y2="18.1"/>');
export const ThreadIcon = makeIcon('<path d="M4 5.5h16"/><path d="M4 11h11"/><path d="M4 16.5h7"/><circle cx="18.5" cy="16.5" r="2.5"/>');
export const PauseIcon = makeIcon('<rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/>');
export const PlayIcon = makeIcon('<polygon points="6 4 20 12 6 20"/>');
export const ShieldIcon = makeIcon('<path d="M12 3 4.5 6v6c0 5 3.4 7.7 7.5 9 4.1-1.3 7.5-4 7.5-9V6Z"/>');
export const ChevronRightIcon = makeIcon('<polyline points="9 5 16 12 9 19"/>');
export const ChevronDownIcon = makeIcon('<polyline points="5 9 12 16 19 9"/>');
export const DotsIcon = makeIcon('<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>');
export const WrenchIcon = makeIcon('<path d="M20 6.5a4.5 4.5 0 0 1-6 4.24L7.5 17.2a1.8 1.8 0 0 1-2.5-2.5l6.46-6.5A4.5 4.5 0 0 1 17.5 2l-3 3 1 2 2 1Z"/>');
export const FolderIcon = makeIcon('<path d="M3.5 6.5h6l1.7 2H20.5v10.5a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1V7.5a1 1 0 0 1 1-1Z"/>');
export const TerminalIcon = makeIcon('<path d="M5 6.5 10 12l-5 5.5"/><line x1="12" y1="17.5" x2="19" y2="17.5"/>');
