"use client";

import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";
import { SettingsTabs } from "@/components/settings/SettingsTabs";

/**
 * Screen 7. Hosts SettingsTabs. Matches the prototype's
 * `overlayShell('Settings', ..., {backTo:'roster'})` -- rendered via the
 * shared `Modal` (desktop-centered / mobile-full-cover), same as the
 * search/activity/new-conversation/diff screens, closing back to the roster.
 */
export default function SettingsPage() {
  const router = useRouter();
  return (
    <Modal title="Settings" onClose={() => router.push("/")}>
      <SettingsTabs />
    </Modal>
  );
}
