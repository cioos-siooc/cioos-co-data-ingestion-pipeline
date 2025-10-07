#!/usr/bin/env python3
"""
Corrected Direct AMQP client using Sarracenia-style queue naming
Based on analysis of Sarracenia source code
"""

import pika
import json
import ssl
import logging
import requests
import socket
import uuid
from pathlib import Path
from marine_buoy_parser import Marine_buoy_parser

# Set up logging
logging.basicConfig(
    level=logging.INFO,  # Normal logging level
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('../logs/eccc_buoys.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class SarraceniaStyleAMQP:
    def __init__(self):
        self.connection = None
        self.channel = None
        self.parser = Marine_buoy_parser()
        self.download_dir = Path("data/marine_buoys/xml")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        
        # Generate persistent Sarracenia-style queue name
        hostname = socket.gethostname()
        # Use 'main' as consistent ID to avoid creating new queues on each restart
        unique_id = "dev2"
        self.queue_name = f"q_anonymous.subscribe.marine_buoys.{hostname}_{unique_id}"
        
    def connect(self):
        """Connect using Sarracenia-style parameters"""
        try:
            credentials = pika.PlainCredentials('anonymous', 'anonymous')
            
            # SSL context
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            parameters = pika.ConnectionParameters(
                host='dd.weather.gc.ca',
                port=5671,
                virtual_host='/',
                credentials=credentials,
                ssl_options=pika.SSLOptions(ssl_context),
                heartbeat=600,
                blocked_connection_timeout=300
            )
            
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            
            logger.info("🌊 Connected to Environment Canada AMQP broker")
            return True
            
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            return False
    
    def setup_queue(self):
        """Set up queue using Sarracenia pattern"""
        try:
            # Declare queue with Sarracenia-style name
            self.channel.queue_declare(
                queue=self.queue_name,
                durable=True,        # Sarracenia uses durable queues
                exclusive=False,     # Not exclusive
                auto_delete=False    # Don't auto-delete
            )
            logger.info(f"📦 Queue declared: {self.queue_name}")
            
            # Bind to marine buoy topics on xpublic exchange
            exchange_name = 'xpublic'
            routing_keys = [
                'v02.post.*.WXO-DD.observations.swob-ml.marine.#',
            ]
            
            for routing_key in routing_keys:
                self.channel.queue_bind(
                    exchange=exchange_name,
                    queue=self.queue_name,
                    routing_key=routing_key
                )
                logger.info(f"📡 Bound {self.queue_name} to {routing_key}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Queue setup failed: {e}")
            return False
    
    def download_file(self, url, filename):
        """Download file from URL"""
        try:
            local_path = self.download_dir / filename
            
            if local_path.exists():
                logger.debug(f"⚠️ File exists: {filename}")
                return local_path
                
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            with open(local_path, 'wb') as f:
                f.write(response.content)
                
            logger.info(f"📥 Downloaded: {filename}")
            return local_path
            
        except Exception as e:
            logger.error(f"❌ Download error {filename}: {e}")
            return None
    
    def parse_file(self, file_path):
        """Parse downloaded XML file"""
        try:
            result = self.parser.parse_marine_xml(file_path)
            self.parser.toCSV()
            
            logger.info(f"✅ Parsed: {file_path.name}")
                
        except Exception as e:
            logger.error(f"❌ Parse error {file_path}: {e}")
    
    def message_callback(self, channel, method, properties, body):
        """Process incoming messages"""
        try:
            # Decode message body
            body_str = body.decode('utf-8').strip()
            
            # Parse Sarracenia v02 format: timestamp base_url rel_path
            parts = body_str.split(' ', 2)  # Split into max 3 parts
            
            if len(parts) < 3:
                logger.error(f"❌ Invalid message format. Expected 3 parts, got {len(parts)}: {body_str}")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return
            
            timestamp, base_url, rel_path = parts
            
            # Validate we have what we need
            if not base_url or not rel_path:
                logger.warning(f"⚠️ Incomplete message: timestamp={timestamp}, base_url={base_url}, rel_path={rel_path}")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return
            
            # Construct full URL
            if base_url.endswith('/'):
                file_url = base_url + rel_path
            else:
                file_url = base_url + '/' + rel_path
            
            filename = Path(rel_path).name
            
            logger.info(f"📨 Processing: {filename} (timestamp: {timestamp})")
            
            # Download and parse
            local_path = self.download_file(file_url, filename)
            if local_path and local_path.suffix.lower() == '.xml':
                self.parse_file(local_path)
            
            # Acknowledge message
            channel.basic_ack(delivery_tag=method.delivery_tag)
            
        except Exception as e:
            logger.error(f"❌ Message processing error: {e}")
            logger.error(f"Message content: {body.decode('utf-8', errors='ignore')[:200]}")
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    
    def start_consuming(self):
        """Start consuming messages"""
        try:
            # Set QoS similar to Sarracenia
            self.channel.basic_qos(prefetch_count=25)
            
            # Set up consumer
            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self.message_callback
            )
            
            logger.info("🚀 Starting to consume marine buoy messages...")
            logger.info(f"Using queue: {self.queue_name}")
            logger.info("Press CTRL+C to stop")
            
            self.channel.start_consuming()
            
        except KeyboardInterrupt:
            logger.info("🛑 Stopping consumer...")
            self.channel.stop_consuming()
        except Exception as e:
            logger.error(f"❌ Consumer error: {e}")
        finally:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.info("🔌 Connection closed")
    
    def run(self):
        """Main run method"""
        logger.info("🌊 Starting Sarracenia-Style AMQP Client...")
        
        if not self.connect():
            return False
            
        if not self.setup_queue():
            return False
            
        self.start_consuming()
        return True

def main():
    client = SarraceniaStyleAMQP()
    client.run()

if __name__ == "__main__":
    main()
