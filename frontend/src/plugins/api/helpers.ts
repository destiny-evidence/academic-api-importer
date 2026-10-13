import { type AccessToken, Tokens } from '@/plugins/api/spec';
import { toastReject, toastSuccessWith } from '@/plugins/api/index.ts';

/**
 * Turns the token itself on or off (`AccessToken.is_active`).
 *
 * The endpoint only modifies fields that are present in the request body, so
 * the secret `token` (which `AccessTokenPublic` never exposes, but the
 * generated `AccessToken` type demands) is left out with a cast; `wrapper`
 * does not need to be resent either. Owners may change nothing but this flag.
 */
export function updateTokenStatus(tokenId: string, active: boolean) {
  return Tokens.updateTokenTokensUpdatePost({
    accessToken: { token_id: tokenId, is_active: active } as AccessToken,
  }).then(toastSuccessWith(active ? 'Token activated' : 'Token deactivated'));
}

export function updateTokenAccess(
  userId: string,
  tokenId: string,
  access: boolean,
  owner: boolean = false,
) {
  if (access)
    Tokens.editPermissionsTokensPermissionsPost({
      body: [
        {
          token_id: tokenId,
          user_id: userId,
          is_owner: owner,
        },
      ],
    })
      .then(toastSuccessWith('Updated permissions'))
      .catch(toastReject);
  else {
    Tokens.deletePermissionsTokensPermissionsDelete({
      body: [
        {
          token_id: tokenId,
          user_id: userId,
        },
      ],
    })
      .then(toastSuccessWith('Deleted permissions'))
      .catch(toastReject);
  }
}
