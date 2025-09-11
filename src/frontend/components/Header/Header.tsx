// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import Link from 'next/link';
import CartIcon from '../CartIcon';
import CurrencySwitcher from '../CurrencySwitcher';
import { useRouter } from 'next/router';
import { useAuth } from '../../providers/Auth.provider';
import * as S from './Header.styled';

const Header = () => {
  const router = useRouter();
  const { user, logout } = useAuth();

  const onLogout = () => {
    logout();
    router.replace('/');
  };

  return (
    <S.Header>
      <S.NavBar>
        <S.Container>
          {/* Colonna sinistra: logo */}
          <S.Left>
            <S.NavBarBrand href="/">
              <S.BrandImg />
            </S.NavBarBrand>
          </S.Left>

          {/* Colonna centrale: login/register o logout */}
          <S.Center>
            {user ? (
              <>
                <span style={{ marginRight: 8 }}>Ciao, {user.username}</span>
                <button onClick={onLogout} style={{ marginRight: 16 }}>
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link href="/login" style={{ marginRight: 12 }}>
                  Login
                </Link>
                <Link href="/register" style={{ marginRight: 16 }}>
                  Register
                </Link>
              </>
            )}
          </S.Center>

          {/* Colonna destra: controlli */}
          <S.Right>
            <CurrencySwitcher />
            <CartIcon />
          </S.Right>
        </S.Container>
      </S.NavBar>
    </S.Header>
  );
};

export default Header;
