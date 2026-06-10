import { createContext, useContext } from 'react';

// Feature flags from GET /api/v1/config (features.*). Lets operators turn whole
// features on/off without a rebuild. Add a flag here and read it with
// useFeature(); the backend source of truth is reporting/settings.py.
export interface Features {
  chat: boolean;
}

// Disabled defaults prevent feature UI from flashing before GET /api/v1/config
// resolves. App.tsx applies BACKEND_DEFAULT_FEATURES after the config response.
export const DEFAULT_FEATURES: Features = {
  chat: false,
};

export const BACKEND_DEFAULT_FEATURES: Features = {
  chat: true,
};

export const FeaturesContext = createContext<Features>(DEFAULT_FEATURES);

export function useFeature(name: keyof Features): boolean {
  return useContext(FeaturesContext)[name];
}
