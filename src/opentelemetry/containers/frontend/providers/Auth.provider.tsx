import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import TokenGateway from '../gateways/Token.gateway';
import AuthGateway from '../gateways/Auth.gateway';

type User = { username: string } | null;

type AuthContextType = {
  user: User;
  token: string | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, email?: string, address?: string, zip?: string, city?: string, state?: string, country?: string, phone?: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User>(null);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    setToken(TokenGateway.getToken());
    const u = TokenGateway.getUser();
    setUser(u);
  }, []);

  const login = async (username: string, password: string) => {
    const result = await AuthGateway.login({ username, password });
    const jwt = result.token;
    TokenGateway.setToken(jwt);
    TokenGateway.setUser({ username });
    setToken(jwt);
    setUser({ username });
  };

  const register = async (username: string, password: string, email?: string, address?: string, zip?: string, city?: string, state?: string, country?: string, phone?: string) => {
    await AuthGateway.register({ username, password, email, address, zip, city, state, country, phone });
    await login(username, password);
  };

  const logout = () => {
    TokenGateway.logout();
    setUser(null);
    setToken(null);
  };

  const isAuthenticated = !!token;

  const value = useMemo(() => ({ user, token, isAuthenticated, login, register, logout }), [user, token]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
};

export default AuthProvider;
