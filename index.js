import nodemailer from "nodemailer";

async function spoofTest({spoofableFrom, to, replyTo}) {
  const transporter = nodemailer.createTransport({
    host: "mail.feyitech.com",
    port: 465,
    secure: true,
    family: 4, // 👈 force IPv4
    auth: {
        user: "hello@feyitech.com",
        pass: "{Feyitech}^94"
    },
    debug: true,
    logger: true,
  });

  try {
    const config = {
      // 👇 SPOOFED sender (your domain)
      from: `"Fake Support" <${spoofableFrom}>`,
      
      // 👇 send to yourself so you can inspect it
      to: to,
      
      subject: "SPF/DMARC Spoof Test",
      text: "If your DNS is configured correctly, this should fail or be flagged."
    }

    if(replyTo) {
        config.headers = {
            "Reply-To": replyTo, // simulate phishing trick
        }
    }
    const info = await transporter.sendMail(config);

    console.log("Message sent:", info.messageId);
    console.log("Response:", info.response);
  } catch (error) {
    console.error("Error sending email:");
    console.error(error);
  }
}

spoofTest(
    {
        spoofableFrom: "hello@feyitech.com",//"info@softbaker.com",  
        to: "cyberockvalley@gmail.com", 
        replyTo: "pheyid@gmail.com"
    }
);