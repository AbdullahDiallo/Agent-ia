-- Mise à jour du template email welcome_candidate
-- Retire: bouton "Accéder au dashboard", liens sociaux, texte "Suivi transparent"
-- Met à jour le numéro de téléphone: +221 77 662 5059

UPDATE email_templates
SET 
  html_template = '<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bienvenue à {{ company_name }}</title>
</head>
<body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #f4f4f4;">
    <table role="presentation" style="width: 100%; border-collapse: collapse;">
        <tr>
            <td align="center" style="padding: 40px 0;">
                <table role="presentation" style="width: 600px; border-collapse: collapse; background-color: #ffffff; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <tr>
                        <td style="padding: 40px 30px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); text-align: center;">
                            <h1 style="margin: 0; color: #ffffff; font-size: 28px; font-weight: bold;">
                                Bienvenue à {{ company_name }} !
                            </h1>
                        </td>
                    </tr>
                    
                    <!-- Content -->
                    <tr>
                        <td style="padding: 40px 30px;">
                            <p style="margin: 0 0 20px; color: #333333; font-size: 16px; line-height: 1.6;">
                                Bonjour <strong>{{ candidate_name }}</strong>,
                            </p>
                            
                            <p style="margin: 0 0 20px; color: #333333; font-size: 16px; line-height: 1.6;">
                                Nous sommes ravis de vous accompagner dans votre parcours d''admission. Votre confiance est précieuse et notre équipe est mobilisée pour la réussite de votre inscription.
                            </p>
                            
                            <div style="background-color: #f8f9fa; border-left: 4px solid #667eea; padding: 20px; margin: 30px 0;">
                                <h2 style="margin: 0 0 15px; color: #667eea; font-size: 18px;">
                                    Ce que nous vous offrons :
                                </h2>
                                <ul style="margin: 0; padding-left: 20px; color: #555555; line-height: 1.8;">
                                    <li>Un accompagnement personnalisé pour votre orientation</li>
                                    <li>Un suivi de dossier clair jusqu''à l''inscription</li>
                                    <li>Des conseils d''experts en admissions et filières</li>
                                </ul>
                            </div>
                            
                            <p style="margin: 30px 0 20px; color: #333333; font-size: 16px; line-height: 1.6;">
                                Notre équipe est à votre écoute pour toute question. N''hésitez pas à nous contacter par téléphone au <strong>{{ company_phone }}</strong> ou par email.
                            </p>
                            
                            <p style="margin: 0; color: #333333; font-size: 16px; line-height: 1.6;">
                                Bienvenue dans la communauté {{ company_name }} !
                            </p>
                            
                            <p style="margin: 20px 0 0; color: #333333; font-size: 16px; line-height: 1.6;">
                                <strong>Toute l''équipe</strong>
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 30px; background-color: #f8f9fa; text-align: center; border-top: 1px solid #e9ecef;">
                            <p style="margin: 0 0 10px; color: #6c757d; font-size: 14px;">
                                {{ company_name }} • {{ company_address }}
                            </p>
                            <p style="margin: 0; color: #6c757d; font-size: 14px;">
                                Téléphone: {{ company_phone }} • Email: {{ company_email }}
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>',
  text_template = 'Bonjour {{ candidate_name }},

Nous sommes ravis de vous accompagner dans votre parcours d''admission.

Votre confiance est précieuse et notre équipe est mobilisée pour la réussite de votre inscription.

Ce que nous vous offrons :
- Un accompagnement personnalisé pour votre orientation
- Un suivi de dossier clair jusqu''à l''inscription
- Des conseils d''experts en admissions et filières

Notre équipe est à votre écoute pour toute question. N''hésitez pas à nous contacter par téléphone au {{ company_phone }} ou par email.

Bienvenue dans la communauté {{ company_name }} !

Toute l''équipe

---
{{ company_name }} • {{ company_address }}
Téléphone: {{ company_phone }} • Email: {{ company_email }}'
WHERE name = 'welcome_candidate';
