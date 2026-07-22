import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { InstallButton } from "./InstallButton";

describe("InstallButton", () => {
  it("offers installation after the browser prompt is available", async () => {
    const prompt = vi.fn().mockResolvedValue(undefined);
    const event = Object.assign(new Event("beforeinstallprompt", { cancelable: true }), {
      prompt,
      userChoice: Promise.resolve({ outcome: "accepted" as const }),
    });
    const user = userEvent.setup();
    render(<InstallButton />);

    window.dispatchEvent(event);
    await user.click(await screen.findByRole("button", { name: "Установить FlowMate" }));

    expect(prompt).toHaveBeenCalledOnce();
  });
});
