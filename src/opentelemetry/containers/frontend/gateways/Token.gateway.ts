const tokenKey = 'auth_token';
const userKey = 'auth_user';

interface IUserInfo {
  username: string;
}

const TokenGateway = () => ({
  getToken(): string | null {
    if (typeof window === 'undefined') return null;
    return localStorage.getItem(tokenKey);
  },
  setToken(token: string | null) {
    if (typeof window === 'undefined') return;
    if (token) localStorage.setItem(tokenKey, token);
    else localStorage.removeItem(tokenKey);
  },
  getUser(): IUserInfo | null {
    if (typeof window === 'undefined') return null;
    const raw = localStorage.getItem(userKey);
    return raw ? (JSON.parse(raw) as IUserInfo) : null;
  },
  setUser(user: IUserInfo | null) {
    if (typeof window === 'undefined') return;
    if (user) localStorage.setItem(userKey, JSON.stringify(user));
    else localStorage.removeItem(userKey);
  },
  logout() {
    this.setToken(null);
    this.setUser(null);
  },
});

export default TokenGateway();
