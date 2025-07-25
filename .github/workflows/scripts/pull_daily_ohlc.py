#!/usr/bin/env python3
"""
Daily OHLC Data Puller for Hyperliquid
Pulls daily OHLC data for all assets, handles initial historical data and daily updates.
Includes Donchian Channel calculations for multiple periods.
"""

import requests
import pandas as pd
import json
import os
from datetime import datetime, timedelta
import time
import pytz
from typing import Dict, List, Any, Optional

class HyperliquidDailyOHLC:
    def __init__(self):
        self.base_url = "https://api.hyperliquid.xyz/info"
        self.data_dir = "data/daily_ohlc"
        self.assets_file = os.path.join(self.data_dir, "assets.json")
        self.session = requests.Session()
        
        # Donchian channel periods
        self.donchian_periods = [5, 10, 20, 30, 60, 90, 150, 250, 360]
        
        # Ensure data directory exists
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Check if this is a manual run with specific parameters
        self.full_historical = os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch' and \
                              os.getenv('FULL_HISTORICAL', '').lower() == 'true'
        self.days_back = int(os.getenv('DAYS_BACK', '1'))
        
        print(f"Initialized HyperliquidDailyOHLC")
        print(f"Full historical: {self.full_historical}")
        print(f"Days back: {self.days_back}")
        print(f"Donchian periods: {self.donchian_periods}")

    def get_all_assets(self) -> List[str]:
        """Get all available assets from Hyperliquid"""
        try:
            response = self.session.post(
                self.base_url,
                json={"type": "meta"},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            # Extract asset names from the universe
            assets = []
            if 'universe' in data:
                for asset_info in data['universe']:
                    if 'name' in asset_info:
                        assets.append(asset_info['name'])
            
            print(f"Found {len(assets)} assets")
            return assets
            
        except Exception as e:
            print(f"Error fetching assets: {e}")
            return []

    def get_historical_ohlc(self, asset: str, start_time: int, end_time: int) -> List[Dict]:
        """Get historical OHLC data for a specific asset with retry logic"""
        max_retries = 3
        retry_delay = 3  # Increased initial delay
        
        for attempt in range(max_retries):
            try:
                payload = {
                    "type": "candleSnapshot",
                    "req": {
                        "coin": asset,
                        "interval": "1d",
                        "startTime": start_time,
                        "endTime": end_time
                    }
                }
                
                response = self.session.post(
                    self.base_url,
                    json=payload,
                    timeout=30
                )
                response.raise_for_status()
                data = response.json()
                
                if isinstance(data, list) and len(data) > 0:
                    return data
                else:
                    print(f"No data returned for {asset}")
                    return []
                    
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 500 and attempt < max_retries - 1:
                    print(f"Server error for {asset}, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff: 3s, 6s, 12s
                    continue
                else:
                    print(f"HTTP Error fetching OHLC data for {asset}: {e}")
                    return []
            except Exception as e:
                print(f"Error fetching OHLC data for {asset}: {e}")
                return []
        
        return []

    def calculate_time_range(self, asset: str) -> tuple:
        """Calculate the time range for data fetching"""
        # Always use UTC for consistency
        current_utc = datetime.now(pytz.UTC)
        
        # For daily OHLC, use end of previous day to avoid partial day issues
        # If it's early in the day, yesterday's data should be complete
        current_date = current_utc.date()
        yesterday = current_date - timedelta(days=1)
        
        print(f"Current UTC time: {current_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        # Check if we have existing data for this asset
        asset_file = os.path.join(self.data_dir, f"{asset}_daily.csv")
        
        if self.full_historical or not os.path.exists(asset_file):
            # Pull maximum historical data (start from 2 years ago)
            start_time = current_utc - timedelta(days=730)
            end_time = current_utc
            print(f"Pulling historical data for {asset} from {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}")
        else:
            # Load existing data to determine the last date
            try:
                existing_df = pd.read_csv(asset_file)
                if not existing_df.empty and 'timestamp' in existing_df.columns:
                    # Get the last timestamp
                    last_timestamp = pd.to_datetime(existing_df['timestamp'].iloc[-1])
                    last_date = last_timestamp.date()
                    
                    print(f"Last data for {asset}: {last_date}")
                    
                    # If we already have data for today, skip
                    if last_date >= current_date:
                        print(f"Data for {asset} is up to date (last: {last_date}, current: {current_date})")
                        return None, None  # Signal no update needed
                    
                    # Start from the day after our last data
                    start_time = datetime.combine(last_date + timedelta(days=1), datetime.min.time())
                    start_time = pytz.UTC.localize(start_time)
                    end_time = current_utc
                    
                    print(f"Updating {asset} from {start_time.strftime('%Y-%m-%d')} to {end_time.strftime('%Y-%m-%d')}")
                else:
                    start_time = current_utc - timedelta(days=self.days_back)
                    end_time = current_utc
                    print(f"No valid existing data for {asset}, pulling {self.days_back} days")
            except Exception as e:
                print(f"Error reading existing data for {asset}: {e}")
                start_time = current_utc - timedelta(days=self.days_back)
                end_time = current_utc
        
        start_timestamp = int(start_time.timestamp() * 1000)
        end_timestamp = int(end_time.timestamp() * 1000)
        
        print(f"API request timestamps: start={start_timestamp}, end={end_timestamp}")
        return start_timestamp, end_timestamp

    def calculate_donchian_channels(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate Donchian channels for multiple periods"""
        if df.empty:
            return df
        
        try:
            for period in self.donchian_periods:
                # Donchian Channel High (highest high over period)
                df[f'donchian_high_{period}'] = df['high'].rolling(
                    window=period, 
                    min_periods=period
                ).max()
                
                # Donchian Channel Low (lowest low over period)
                df[f'donchian_low_{period}'] = df['low'].rolling(
                    window=period, 
                    min_periods=period
                ).min()
                
                # Donchian Channel Mid (average of high and low)
                df[f'donchian_mid_{period}'] = (
                    df[f'donchian_high_{period}'] + df[f'donchian_low_{period}']
                ) / 2
            
            print(f"Calculated Donchian channels for periods: {self.donchian_periods}")
            return df
            
        except Exception as e:
            print(f"Error calculating Donchian channels: {e}")
            return df

    def process_ohlc_data(self, raw_data: List[Dict], asset: str) -> pd.DataFrame:
        """Process raw OHLC data into a DataFrame with Donchian channels"""
        if not raw_data:
            return pd.DataFrame()
        
        try:
            # Convert to DataFrame
            df = pd.DataFrame(raw_data)
            
            # Ensure we have the required columns
            required_columns = ['T', 'o', 'h', 'l', 'c', 'v']
            if not all(col in df.columns for col in required_columns):
                print(f"Missing required columns for {asset}")
                return pd.DataFrame()
            
            # Rename columns to standard format
            df = df.rename(columns={
                'T': 'timestamp',
                'o': 'open',
                'h': 'high',
                'l': 'low',
                'c': 'close',
                'v': 'volume'
            })
            
            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            # Convert numeric columns
            numeric_columns = ['open', 'high', 'low', 'close', 'volume']
            for col in numeric_columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Sort by timestamp
            df = df.sort_values('timestamp')
            
            # Add asset column
            df['asset'] = asset
            
            # Calculate Donchian channels
            df = self.calculate_donchian_channels(df)
            
            return df
            
        except Exception as e:
            print(f"Error processing data for {asset}: {e}")
            return pd.DataFrame()

    def save_asset_data(self, df: pd.DataFrame, asset: str):
        """Save or update asset data"""
        if df.empty:
            print(f"No data to save for {asset}")
            return
        
        asset_file = os.path.join(self.data_dir, f"{asset}_daily.csv")
        
        try:
            if os.path.exists(asset_file) and not self.full_historical:
                # Load existing data and append new data
                existing_df = pd.read_csv(asset_file)
                existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'])
                
                # Combine and remove duplicates
                combined_df = pd.concat([existing_df, df], ignore_index=True)
                combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='last')
                combined_df = combined_df.sort_values('timestamp')
                
                # Recalculate Donchian channels for the entire combined dataset
                # (This ensures rolling calculations are correct across the full history)
                combined_df = self.recalculate_donchian_for_existing(combined_df)
                
                # Save combined data
                combined_df.to_csv(asset_file, index=False)
                print(f"Updated {asset} data: {len(df)} new rows, {len(combined_df)} total rows")
            else:
                # Save new data (already has Donchian channels calculated)
                df.to_csv(asset_file, index=False)
                print(f"Saved {asset} data: {len(df)} rows")
                
        except Exception as e:
            print(f"Error saving data for {asset}: {e}")

    def recalculate_donchian_for_existing(self, df: pd.DataFrame) -> pd.DataFrame:
        """Recalculate Donchian channels for existing data that might not have them"""
        try:
            # Remove any existing Donchian columns
            donchian_cols = [col for col in df.columns if col.startswith('donchian_')]
            if donchian_cols:
                df = df.drop(columns=donchian_cols)
            
            # Recalculate all Donchian channels
            return self.calculate_donchian_channels(df)
        except Exception as e:
            print(f"Error recalculating Donchian channels: {e}")
            return df

    def update_assets_list(self, assets: List[str]):
        """Update the assets list file"""
        try:
            assets_data = {
                "assets": assets,
                "last_updated": datetime.now(pytz.UTC).isoformat(),
                "total_assets": len(assets),
                "donchian_periods": self.donchian_periods
            }
            
            with open(self.assets_file, 'w') as f:
                json.dump(assets_data, f, indent=2)
            
            print(f"Updated assets list with {len(assets)} assets")
            
        except Exception as e:
            print(f"Error updating assets list: {e}")

    def create_summary_file(self, successful_assets: List[str], failed_assets: List[str]):
        """Create a summary file of the data pull operation"""
        summary = {
            "timestamp": datetime.now(pytz.UTC).isoformat(),
            "successful_assets": successful_assets,
            "failed_assets": failed_assets,
            "total_successful": len(successful_assets),
            "total_failed": len(failed_assets),
            "operation_type": "full_historical" if self.full_historical else "daily_update",
            "donchian_periods": self.donchian_periods
        }
        
        summary_file = os.path.join(self.data_dir, "last_run_summary.json")
        
        try:
            with open(summary_file, 'w') as f:
                json.dump(summary, f, indent=2)
            
            print(f"Created summary: {len(successful_assets)} successful, {len(failed_assets)} failed")
            
        except Exception as e:
            print(f"Error creating summary: {e}")

    def run(self):
        """Main execution function"""
        print("Starting Daily OHLC Data Pull with Donchian Channels")
        print("=" * 60)
        
        # Get all assets
        assets = self.get_all_assets()
        if not assets:
            print("No assets found. Exiting.")
            return
        
        # Update assets list
        self.update_assets_list(assets)
        
        successful_assets = []
        failed_assets = []
        
        # Process each asset
        for i, asset in enumerate(assets, 1):
            print(f"\nProcessing {asset} ({i}/{len(assets)})")
            
            try:
                # Calculate time range
                start_time, end_time = self.calculate_time_range(asset)
                
                # Skip if no update needed
                if start_time is None and end_time is None:
                    print(f"Skipping {asset} - data is up to date")
                    successful_assets.append(asset)
                    continue
                
                # Get OHLC data
                raw_data = self.get_historical_ohlc(asset, start_time, end_time)
                
                if raw_data:
                    # Process and save data (includes Donchian channel calculations)
                    df = self.process_ohlc_data(raw_data, asset)
                    self.save_asset_data(df, asset)
                    successful_assets.append(asset)
                else:
                    print(f"No data available for {asset}")
                    failed_assets.append(asset)
                
                # Rate limiting - respecting Hyperliquid's 1200 weight/minute limit
                # candleSnapshot = 20 weight, so max 60 requests/minute = 1 per second
                time.sleep(1.2)  # Slightly more than 1 second for safety
                
            except Exception as e:
                print(f"Error processing {asset}: {e}")
                failed_assets.append(asset)
                continue
        
        # Create summary
        self.create_summary_file(successful_assets, failed_assets)
        
        print("\n" + "=" * 60)
        print("Daily OHLC Data Pull Complete")
        print(f"Successful: {len(successful_assets)}")
        print(f"Failed: {len(failed_assets)}")
        print(f"Donchian periods calculated: {self.donchian_periods}")

if __name__ == "__main__":
    puller = HyperliquidDailyOHLC()
    puller.run()
