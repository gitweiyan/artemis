package com.artemis.helper;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.os.Process;

import java.util.regex.Pattern;

/**
 * Accepts the host session token only from the authenticated ADB shell UID.
 */
public final class TokenProvider extends ContentProvider {

    public static final String AUTHORITY = "com.artemis.helper.token";
    public static final String METHOD_SET_TOKEN = "set_session_token";
    private static final Pattern TOKEN_PATTERN = Pattern.compile("[0-9a-f]{32,128}");

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        if (Binder.getCallingUid() != Process.SHELL_UID) {
            throw new SecurityException("Only the ADB shell may set the helper session token");
        }
        if (!METHOD_SET_TOKEN.equals(method) || arg == null || !TOKEN_PATTERN.matcher(arg).matches()) {
            throw new IllegalArgumentException("Invalid session token request");
        }

        TokenStore.set(arg);
        Bundle result = new Bundle();
        result.putString("result", "token_set");
        result.putBoolean("token_set", TokenStore.isSet());
        return result;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs,
            String sortOrder) {
        throw new UnsupportedOperationException();
    }

    @Override
    public String getType(Uri uri) {
        throw new UnsupportedOperationException();
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }
}
