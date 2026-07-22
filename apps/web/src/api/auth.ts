import { apiRequest } from "./client";

export interface AuthenticatedUser {
  id: string;
  display_name: string | null;
}

export interface LoginCodeResponse {
  status: "code_sent";
  expires_in_seconds: number;
}

export const sessionQueryKey = ["pwa-session"] as const;

export function requestLoginCode(): Promise<LoginCodeResponse> {
  return apiRequest<LoginCodeResponse>("/api/v1/auth/login-code", { method: "POST" });
}

export function verifyLoginCode(code: string): Promise<AuthenticatedUser> {
  return apiRequest<AuthenticatedUser>("/api/v1/auth/session", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function getCurrentUser(): Promise<AuthenticatedUser> {
  return apiRequest<AuthenticatedUser>("/api/v1/auth/me");
}

export function logout(): Promise<void> {
  return apiRequest<void>("/api/v1/auth/session", { method: "DELETE" });
}
