import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react';
import axios from 'axios';
import { setAuthToken, getAuthToken } from '../services/api';

export interface AuthUser {
  id: number;
  name: string;
  phone: string;
  role: 'PASSENGER' | 'RAILWAY_STAFF';
  created_at?: string;
}

interface AuthContextType {
  user: AuthUser | null;
  loading: boolean;
  login: (phone: string, password: string) => Promise<{ success: boolean; role?: string; error?: string }>;
  register: (data: RegisterData) => Promise<{ success: boolean; error?: string }>;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  isStaff: boolean;
  isPassenger: boolean;
}

interface RegisterData {
  name: string;
  phone: string;
  password: string;
  confirm_password: string;
}

const AuthContext = createContext<AuthContextType | null>(null);

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const authApi = axios.create({
  baseURL: API_BASE,
  withCredentials: true,  // Send cookies
});

authApi.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token && !config.headers['Authorization']) {
    config.headers['Authorization'] = `Bearer ${token}`;
  }
  return config;
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Check authentication on mount
  const checkAuth = useCallback(async () => {
    try {
      const res = await authApi.get('/auth/me');
      setUser(res.data);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  const login = async (phone: string, password: string) => {
    try {
      const res = await authApi.post('/auth/login', { phone, password });
      if (res.data.access_token) {
        setAuthToken(res.data.access_token);
      }
      setUser(res.data.user);
      return { success: true, role: res.data.user.role as string };
    } catch (err: any) {
      const message = err?.response?.data?.detail || 'Login failed. Please try again.';
      return { success: false, error: message };
    }
  };

  const register = async (data: RegisterData) => {
    try {
      const res = await authApi.post('/auth/register', data);
      if (res.data.access_token) {
        setAuthToken(res.data.access_token);
      }
      setUser(res.data.user);
      return { success: true };
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      let message = 'Registration failed. Please try again.';
      if (typeof detail === 'string') {
        message = detail;
      } else if (Array.isArray(detail)) {
        // Pydantic validation errors
        message = detail.map((e: any) => e.msg || e.message || String(e)).join('. ');
      }
      return { success: false, error: message };
    }
  };

  const logout = async () => {
    try {
      await authApi.post('/auth/logout');
    } catch {
      // Even if the server call fails, clear local state
    }
    setAuthToken(null);
    setUser(null);
  };

  const value: AuthContextType = {
    user,
    loading,
    login,
    register,
    logout,
    isAuthenticated: !!user,
    isStaff: user?.role === 'RAILWAY_STAFF',
    isPassenger: user?.role === 'PASSENGER',
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
