const path = require('path');
const HtmlWebpackPlugin = require('html-webpack-plugin');
const ESLintPlugin = require('eslint-webpack-plugin');
const CopyPlugin = require('copy-webpack-plugin');
const nodeExternals = require('webpack-node-externals');

// Common configuration for both frontend and backend
const commonConfig = {
    mode: 'development',
    devtool: 'source-map',
    module: {
      rules: [
        // Add any shared loaders here
        {
          test: /\.jsx?$/,
          exclude: /node_modules/,
          use: 'babel-loader'
        }
      ]
    },
    resolve: {
      extensions: ['.js', '.jsx'],
      alias: {
        '@server': path.resolve(__dirname, 'server/'),
      }
    }
  };

// Frontend specific configuration
const frontendConfig = {
    ...commonConfig,
    name: 'frontend',
    entry: {
      index: './src/index.js',
    },
    output: {
      filename: '[name].bundle.js',
      path: path.resolve(__dirname, 'dist'),
      clean: true,
    },
    target: 'web',
    resolve: {
      ...commonConfig.resolve,
      fallback: {
        "dgram": false,
        "fs": false,
        "path": require.resolve("path-browserify"),
        "stream": require.resolve("stream-browserify"),
      }
    },
    plugins: [
      new ESLintPlugin({
        extensions: ['js'],
        eslintPath: require.resolve('eslint'),
        overrideConfigFile: path.resolve(__dirname, './eslint.config.cjs'),
      }),
      
      new HtmlWebpackPlugin({
        template: './src/index.html',
        filename: 'index.html',
        chunks: ['index']
      }),
    ],
    devServer: {
      static: {
        directory: path.join(__dirname, 'dist'),
      },
      host: '0.0.0.0',
      server: 'https',
      compress: true,
      port: 8081,
      client: {
        overlay: { warnings: false, errors: true },
      },
      proxy: [{
        context: [
          '/api',
          '/drone',
          '/upload',
          '/getTT',
          '/set',
          '/greenball',
          '/qrcode',
          '/obj',
          '/opti-track'
        ],
        target: 'http://localhost:8000',
        changeOrigin: true,
      }],
    },
  };

  // Backend specific configuration
const backendConfig = {
    ...commonConfig,
    name: 'backend',
    entry: {
      // main server entry point - adjust as needed
      server: './server/main.js', 
      droneControl: './server/MASControl.js',
    },
    output: {
      filename: '[name].js',
      path: path.resolve(__dirname, 'dist'),
    },
    target: 'node',
    externals: [nodeExternals()],
    plugins: [
      new CopyPlugin({
        patterns: [
          { from: 'server/client.js', to: './' },
          // Copy any additional server files that need to be in dist
          // but aren't part of the webpack bundling process
        ],
      }),
    ],
  };

// For dev purpose build, run frontend configs
// For production build, run both configs
module.exports = process.env.NODE_ENV === 'production' 
? [frontendConfig, backendConfig]
: frontendConfig;